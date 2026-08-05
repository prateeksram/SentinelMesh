package com.sentinelmesh.gesturefootball

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Matrix
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.util.Log
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import com.sentinelmesh.gesturefootball.calibrate.CalibrationSession
import com.sentinelmesh.gesturefootball.databinding.ActivityMainBinding
import com.sentinelmesh.gesturefootball.forcepose.ForcePoseEngine
import com.sentinelmesh.gesturefootball.net.GameClient
import com.sentinelmesh.gesturefootball.pose.PoseAnalyzer
import com.sentinelmesh.gesturefootball.profile.PlayerProfile
import com.sentinelmesh.gesturefootball.profile.PlayerProfileStore
import com.sentinelmesh.gesturefootball.voice.QwenCoach
import com.sentinelmesh.gesturefootball.voice.VoiceCoach
import com.sentinelmesh.gesturefootball.voice.VoiceListener
import com.sentinelmesh.gesturefootball.voice.WhisperEngine
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.Executors
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.roundToInt

class MainActivity : AppCompatActivity(), GameClient.Listener {

    private lateinit var binding: ActivityMainBinding
    private lateinit var game: GameClient
    private var pose: PoseAnalyzer? = null
    private var voice: VoiceListener? = null
    private var coach: VoiceCoach? = null
    private var qwen: QwenCoach? = null
    private val cameraExecutor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())

    private var lastZoneSent = 0L
    private val skelBuf = ArrayDeque<Pair<Long, List<FloatArray>>>()
    private var lastPhase: String? = null
    private var lastResultSpoken: String? = null

    private var calibrating = false
    private var calib: CalibrationSession? = null
    private var profile: PlayerProfile? = null
    private var pendingCalibKick: ForcePoseEngine.KickEvent? = null

    private var lastAsrMs: Long = -1
    private var lastLlmMs: Long = -1
    private val zoneHistory = ArrayDeque<String>()
    private var lastNotedZone: String? = null
    private var lastKickMeta: ForcePoseEngine.KickEvent? = null

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { result ->
        if (result[Manifest.permission.CAMERA] == true) startCamera()
        else binding.hint.text = "Camera permission required"
        if (result[Manifest.permission.RECORD_AUDIO] == true) startVoice()
        else binding.voiceBadge.text = "VOICE · NO MIC"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val prefs = getSharedPreferences(GameClient.PREFS, Context.MODE_PRIVATE)
        val savedUrl = prefs.getString(GameClient.PREF_URL, GameClient.DEFAULT_URL)
            ?: GameClient.DEFAULT_URL
        binding.hostUrl.setText(savedUrl.removePrefix("ws://").removeSuffix("/ws").let {
            if (savedUrl == GameClient.DEFAULT_URL) "127.0.0.1:8080" else it
        })
        game = GameClient(url = GameClient.normalizeUrl(savedUrl), listener = this)
        game.connect()
        binding.hostConnect.setOnClickListener { connectHost() }
        binding.hostUrl.setOnEditorActionListener { _, action, _ ->
            if (action == EditorInfo.IME_ACTION_DONE) {
                connectHost(); true
            } else false
        }

        try {
            pose = PoseAnalyzer(
                context = this,
                onHud = { hud -> mainHandler.post { applyHud(hud) } },
                onKick = { kick -> mainHandler.post { handleKick(kick) } },
                onSkeleton = { t, pts ->
                    skelBuf.addLast(t to pts)
                    while (skelBuf.isNotEmpty() && t - skelBuf.first().first > 1400) skelBuf.removeFirst()
                },
            )
            binding.aiBadge.text = "BODY AI · ON-DEVICE"
            binding.npuBadge.text = "DELEGATE · ${pose?.delegateLabel} · tap"
            binding.npuBadge.setOnClickListener {
                val label = pose?.cycleDelegate() ?: return@setOnClickListener
                binding.npuBadge.text = "DELEGATE · $label · …"
                vibrate(25)
            }
        } catch (e: Exception) {
            binding.aiBadge.text = "AI FAILED"
            binding.hint.text = "Model missing or GPU delegate failed: ${e.message}"
        }

        profile = PlayerProfileStore.load(this)
        profile?.let { pose?.applyProfile(it) }
        qwen = QwenCoach(this).also { it.setProfile(profile) }
        updateProfileHint()
        refreshNeuralLoad()

        binding.calibBtn.setOnClickListener { startCalibration() }
        binding.calibration.calibNext.setOnClickListener { onCalibNext() }
        binding.calibration.calibSkip.setOnClickListener { onCalibSkip() }

        if (profile == null) startCalibration()

        val need = mutableListOf<String>()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            != PackageManager.PERMISSION_GRANTED
        ) need += Manifest.permission.CAMERA
        else startCamera()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) need += Manifest.permission.RECORD_AUDIO
        else startVoice()
        if (need.isNotEmpty()) permissionLauncher.launch(need.toTypedArray())
    }

    private var hostTapGuardUntil = 0L
    /** True after HOST tap until Connected / Failed — avoids auto-reconnect overwriting match hints. */
    private var hostStatusPending = false
    /** Keep connect result on the hint long enough to read (lobby state otherwise replaces it instantly). */
    private var hostHintHoldUntil = 0L

    private fun hintHeldByHost(): Boolean = System.currentTimeMillis() < hostHintHoldUntil

    private fun showHostHint(text: String, colorRes: Int, holdMs: Long = 4500L) {
        binding.hint.setTextColor(ContextCompat.getColor(this, colorRes))
        binding.hint.text = text
        hostHintHoldUntil = System.currentTimeMillis() + holdMs
    }

    private fun connectHost() {
        val now = System.currentTimeMillis()
        if (now < hostTapGuardUntil) return
        hostTapGuardUntil = now + 2000L
        binding.hostConnect.isEnabled = false
        mainHandler.postDelayed({
            if (!isFinishing) binding.hostConnect.isEnabled = true
        }, 2000)

        val raw = binding.hostUrl.text?.toString().orEmpty().trim()
        hostStatusPending = true
        if (raw.isEmpty()) {
            flashHostBtn("ERR")
            showHostHint(
                "Host failed · Enter laptop IP, e.g. 172.20.10.2:8080 · check Wi‑Fi & server.py",
                R.color.red,
            )
            game.reconnect("")
            hostStatusPending = false
            vibrate(40)
            return
        }
        val url = GameClient.normalizeUrl(raw)
        getSharedPreferences(GameClient.PREFS, Context.MODE_PRIVATE)
            .edit().putString(GameClient.PREF_URL, url).apply()
        showHostHint("Connecting $url …", R.color.cyan, holdMs = 8000L)
        game.reconnect(url)
        vibrate(30)
    }

    private fun flashHostBtn(label: String) {
        val btn = binding.hostConnect
        btn.text = label
        mainHandler.postDelayed({
            if (!isFinishing) btn.text = "HOST"
        }, 1500)
    }

    override fun onConnectStatus(status: GameClient.ConnectStatus) {
        mainHandler.post {
            when (status) {
                is GameClient.ConnectStatus.Connecting -> {
                    if (!hostStatusPending) return@post
                    showHostHint("Connecting ${status.url} …", R.color.cyan, holdMs = 8000L)
                }
                is GameClient.ConnectStatus.Connected -> {
                    if (!hostStatusPending) return@post
                    hostStatusPending = false
                    flashHostBtn("OK")
                    showHostHint("Connected · ${status.url}", R.color.green)
                }
                is GameClient.ConnectStatus.Failed -> {
                    hostStatusPending = false
                    flashHostBtn("ERR")
                    showHostHint(
                        "Host failed · ${status.message} · check Wi‑Fi & server.py",
                        R.color.red,
                    )
                }
                is GameClient.ConnectStatus.Disconnected -> {
                    if (!hostStatusPending) return@post
                    hostStatusPending = false
                    showHostHint("Disconnected · ${status.url}", R.color.muted)
                }
            }
        }
    }

    private fun startVoice() {
        if (voice != null) return
        binding.voiceBadge.text = "VOICE · LOADING"
        Executors.newSingleThreadExecutor().execute {
            val engine = WhisperEngine.create(this)
            val smoke = if (engine != null) smokeWhisper(engine) else null
            mainHandler.post {
                if (engine == null) {
                    binding.voiceBadge.text = "VOICE · NO MODEL"
                    return@post
                }
                coach = VoiceCoach(this)
                if (smoke != null) handleVoice(smoke)
                voice = VoiceListener(
                    engine = engine,
                    onResult = { r ->
                        lastAsrMs = r.latencyMs
                        refreshNeuralLoad()
                        handleVoice(r)
                    },
                    onStatus = { s -> binding.voiceBadge.text = s },
                )
                voice?.start()
            }
        }
    }

    private fun smokeWhisper(engine: WhisperEngine): WhisperEngine.Result? {
        val melFile = File(filesDir, "whisper/smoke.mel")
        val pcmFile = File(filesDir, "whisper/smoke.pcm")
        return try {
            val r = when {
                melFile.isFile -> {
                    val bytes = melFile.readBytes()
                    val mel = FloatArray(bytes.size / 4)
                    ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer().get(mel)
                    melFile.delete()
                    engine.transcribeMel(mel)
                }
                pcmFile.isFile -> {
                    val bytes = pcmFile.readBytes()
                    val pcm = ShortArray(bytes.size / 2)
                    ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(pcm)
                    pcmFile.delete()
                    engine.transcribe(pcm)
                }
                else -> null
            }
            if (r != null) {
                lastAsrMs = r.latencyMs
                Log.i("VoiceSmoke", "smoke → \"${r.text}\" · ${r.latencyMs} ms")
            }
            r
        } catch (e: Exception) {
            Log.e("VoiceSmoke", "smoke failed", e)
            null
        }
    }

    private fun handleVoice(result: WhisperEngine.Result) {
        val cmd = coach?.parse(result.text) ?: return
        when (cmd.intent) {
            VoiceCoach.Intent.READY -> {
                binding.big.text = "READY"
                binding.hint.text = "Heard: \"${result.text}\""
                vibrate(60)
                askQwen("ready")
            }
            VoiceCoach.Intent.LEFT -> {
                pose?.forceZone("L")
                setZone("L")
                binding.hint.text = "Voice aim · L · \"${result.text}\""
            }
            VoiceCoach.Intent.CENTER -> {
                pose?.forceZone("C")
                setZone("C")
                binding.hint.text = "Voice aim · C · \"${result.text}\""
            }
            VoiceCoach.Intent.RIGHT -> {
                pose?.forceZone("R")
                setZone("R")
                binding.hint.text = "Voice aim · R · \"${result.text}\""
            }
            VoiceCoach.Intent.TRASH -> {
                binding.hint.text = "Trash talk · \"${result.text}\""
                vibrate(40)
                askQwen("trash talk")
            }
            VoiceCoach.Intent.UNKNOWN -> {
                if (result.text.isNotBlank()) {
                    binding.hint.text = "Heard: \"${result.text}\""
                }
            }
        }
        if (cmd.reply.isNotBlank()) coach?.speak(cmd.reply)
    }

    private fun askQwen(event: String) {
        val q = qwen ?: return
        q.adviseAsync(event) { reply ->
            mainHandler.post {
                lastLlmMs = reply.latencyMs
                refreshNeuralLoad()
                if (reply.text.isNotBlank()) {
                    coach?.speak(reply.text)
                    if (!calibrating) {
                        binding.hint.text = "Coach · ${reply.text}"
                    }
                }
            }
        }
    }

    private fun refreshNeuralLoad() {
        val poseMs = pose?.lastPoseMs?.takeIf { it >= 0 }?.toString() ?: "—"
        val asr = if (lastAsrMs >= 0) "${lastAsrMs}" else "—"
        val llm = if (lastLlmMs >= 0) "${lastLlmMs}" else "—"
        val backend = qwen?.backendLabel ?: "—"
        binding.neuralLoad.text = "NEURAL LOAD · POSE ${poseMs}ms · ASR ${asr}ms · LLM ${llm}ms ($backend)"
    }

    private fun noteZone(zone: String) {
        if (zone == lastNotedZone) return
        lastNotedZone = zone
        zoneHistory.addLast(zone)
        while (zoneHistory.size > 6) zoneHistory.removeFirst()
        if (zoneHistory.size >= 4) {
            val last4 = zoneHistory.takeLast(4)
            if (last4.distinct().size == 1 && !calibrating) {
                binding.hint.text = "You're predictable — mix it. Stop looping $zone."
            }
        }
    }

    private fun updateProfileHint() {
        if (hintHeldByHost()) return
        val p = profile
        if (p == null) {
            binding.hint.text = "Calibrate once — private profile on this phone."
        } else if (!calibrating) {
            binding.hint.text =
                "Profile · ${p.weightKg.roundToInt()} kg · torso ${"%.2f".format(p.torsoM)} m · ${p.dominantFoot} foot"
        }
    }

    private fun startCalibration() {
        calibrating = true
        calib = CalibrationSession()
        pendingCalibKick = null
        pose?.calibrationSwing = false
        binding.calibration.calibPanel.visibility = View.VISIBLE
        binding.calibBtn.visibility = View.GONE
        refreshCalibUi()
    }

    private fun finishCalibration(save: Boolean) {
        val session = calib
        if (save && session != null && session.step == CalibrationSession.Step.DONE) {
            val p = session.buildProfile()
            PlayerProfileStore.save(this, p)
            profile = p
            pose?.applyProfile(p)
            qwen?.setProfile(p)
            vibrate(80)
        }
        calibrating = false
        calib = null
        pendingCalibKick = null
        pose?.calibrationSwing = false
        binding.overlay.setBodyGuide(show = false, ok = false)
        binding.calibration.calibPanel.visibility = View.GONE
        binding.calibBtn.visibility = View.VISIBLE
        updateProfileHint()
        if (lastPhase == null || lastPhase == "lobby") {
            binding.big.text = "READY?"
        }
    }

    private fun refreshCalibUi() {
        val ui = calib?.ui() ?: return
        val panel = binding.calibration
        panel.calibTitle.text = ui.title
        panel.calibHint.text = ui.hint
        panel.calibProgress.progress = (ui.progress * 100).roundToInt()
        panel.calibProgress.visibility =
            if (ui.showBiometrics) View.GONE else View.VISIBLE
        panel.calibProgress.progressTintList = ContextCompat.getColorStateList(
            this,
            when {
                ui.holding -> R.color.green
                ui.title.contains("DETECTED") -> R.color.cyan
                else -> R.color.amber
            }
        )
        panel.calibHint.setTextColor(
            ContextCompat.getColor(
                this,
                when {
                    ui.holding -> R.color.green
                    ui.title.contains("DETECTED") -> R.color.cyan
                    ui.title.contains("FIND") -> R.color.amber
                    else -> R.color.muted
                }
            )
        )
        panel.calibBioRow.visibility = if (ui.showBiometrics) View.VISIBLE else View.GONE

        when (ui.step) {
            CalibrationSession.Step.BIOMETRICS -> {
                panel.calibNext.text = getString(R.string.calib_next)
                panel.calibSkip.visibility = View.INVISIBLE
                pose?.calibrationSwing = false
            }
            CalibrationSession.Step.PRACTICE -> {
                pose?.calibrationSwing = true
                pose?.setKickThreshold(1.8f)
                panel.calibSkip.visibility = View.VISIBLE
                panel.calibSkip.text = "RESTART"
                panel.calibNext.text = getString(R.string.calib_confirm_swing)
                panel.calibNext.isEnabled = ui.canFinishSwing
                panel.calibNext.alpha = if (ui.canFinishSwing) 1f else 0.45f
                binding.big.text = "KICK!"
                binding.hint.text = ui.hint
            }
            CalibrationSession.Step.DONE -> {
                pose?.calibrationSwing = false
                panel.calibSkip.visibility = View.INVISIBLE
                panel.calibNext.text = getString(R.string.calib_done)
                panel.calibNext.isEnabled = true
                panel.calibNext.alpha = 1f
                binding.big.text = "LOCKED IN"
            }
            else -> {
                pose?.calibrationSwing = false
                panel.calibSkip.visibility = View.VISIBLE
                panel.calibSkip.text = getString(R.string.calib_skip)
                panel.calibNext.text = if (ui.holding) "HOLD…" else "AUTO"
                panel.calibNext.isEnabled = false
                panel.calibNext.alpha = 0.45f
                binding.big.text = ui.title
                binding.hint.text = ui.hint
            }
        }
    }

    private fun onCalibNext() {
        val session = calib ?: return
        when (session.step) {
            CalibrationSession.Step.BIOMETRICS -> {
                val h = binding.calibration.calibHeight.text.toString().toFloatOrNull() ?: 175f
                val w = binding.calibration.calibWeight.text.toString().toFloatOrNull() ?: 75f
                session.submitBiometrics(h, w)
                refreshCalibUi()
            }
            CalibrationSession.Step.PRACTICE -> {
                session.confirmPractice()
                refreshCalibUi()
            }
            CalibrationSession.Step.DONE -> finishCalibration(save = true)
            else -> Unit
        }
    }

    private fun onCalibSkip() {
        val session = calib ?: return
        when (session.step) {
            CalibrationSession.Step.PRACTICE -> startCalibration()
            CalibrationSession.Step.AIM_L,
            CalibrationSession.Step.AIM_C,
            CalibrationSession.Step.AIM_R,
            -> {
                session.skipAimDefaults()
                refreshCalibUi()
            }
            else -> Unit
        }
    }

    private fun startCamera() {
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            val provider = future.get()
            val preview = Preview.Builder().build().also {
                it.surfaceProvider = binding.preview.surfaceProvider
            }
            val analysis = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
                .build()
            analysis.setAnalyzer(cameraExecutor) { imageProxy ->
                try {
                    val bitmap = imageProxy.toBitmap()
                    val rotated = rotate(bitmap, imageProxy.imageInfo.rotationDegrees)
                    pose?.analyze(rotated, System.currentTimeMillis())
                } catch (_: Exception) {
                } finally {
                    imageProxy.close()
                }
            }
            provider.unbindAll()
            provider.bindToLifecycle(
                this,
                CameraSelector.DEFAULT_FRONT_CAMERA,
                preview,
                analysis,
            )
        }, ContextCompat.getMainExecutor(this))
    }

    private fun rotate(src: Bitmap, degrees: Int): Bitmap {
        if (degrees == 0) return src
        val m = Matrix().apply { postRotate(degrees.toFloat()) }
        return Bitmap.createBitmap(src, 0, 0, src.width, src.height, m, true)
    }

    private fun applyHud(hud: PoseAnalyzer.Hud) {
        binding.overlay.setLandmarks(hud.landmarks)
        setZone(hud.zone)
        noteZone(hud.zone)
        val framed = hud.inGuide || (
            hud.bodyOk && hud.bodyOkStreak >= PoseAnalyzer.BODY_OK_FRAMES
            )
        binding.bodyBadge.text = when {
            framed -> "FULL BODY ✓"
            hud.landmarks != null -> "FIT THE OUTLINE"
            else -> "HOLD LIKE A MIRROR"
        }
        binding.bodyBadge.setTextColor(
            ContextCompat.getColor(
                this,
                if (framed) R.color.green else R.color.red
            )
        )
        val guideOk = hud.inGuide || (
            calibrating && hud.landmarks != null && hud.bodyOkStreak >= 3
            )
        binding.overlay.setBodyGuide(
            show = calibrating && calib?.step != CalibrationSession.Step.BIOMETRICS &&
                calib?.step != CalibrationSession.Step.DONE,
            ok = guideOk,
        )
        binding.forceBadge.text = if (hud.liveForce > 5f)
            "FORCEPOSE · ${hud.liveForce.roundToInt()} N"
        else "FORCEPOSE · — N"
        binding.npuBadge.text = "DELEGATE · ${hud.delegateLabel} · ${hud.latencyMs} ms"
        refreshNeuralLoad()

        if (calibrating) {
            val session = calib ?: return
            val kick = pendingCalibKick
            pendingCalibKick = null
            val lm = hud.landmarks
            val advanced = session.onPose(
                nowMs = System.currentTimeMillis(),
                // Any pose mesh is enough to leave FIND YOU; T-pose/aim gates the hold.
                bodyOk = lm != null,
                landmarks = lm,
                wristXMirrored = hud.wristXMirrored,
                liveForce = hud.liveForce,
                kick = kick,
                kickFoot = kick?.foot ?: hud.liveFoot,
                footSpeed = max(hud.liveSpeed, kick?.peakSpeed ?: 0f),
            )
            if (advanced || session.step == CalibrationSession.Step.PRACTICE ||
                session.step == CalibrationSession.Step.TPOSE ||
                session.step == CalibrationSession.Step.AIM_L ||
                session.step == CalibrationSession.Step.AIM_C ||
                session.step == CalibrationSession.Step.AIM_R
            ) {
                if (advanced) vibrate(40)
                refreshCalibUi()
            }
            return
        }

        val now = System.currentTimeMillis()
        if (now - lastZoneSent > 200) {
            game.sendAim(hud.zone)
            lastZoneSent = now
        }
    }

    private fun setZone(zone: String) {
        fun chip(v: TextView, on: Boolean) {
            v.setBackgroundResource(if (on) R.drawable.zone_on else R.drawable.zone_off)
            v.setTextColor(
                ContextCompat.getColor(this, if (on) R.color.chalk else R.color.muted)
            )
        }
        chip(binding.zL, zone == "L")
        chip(binding.zC, zone == "C")
        chip(binding.zR, zone == "R")
    }

    private fun handleKick(kick: ForcePoseEngine.KickEvent) {
        if (calibrating) {
            pendingCalibKick = kick
            binding.big.text = "SWING ${kick.forceN} N"
            vibrate(100)
            return
        }
        lastKickMeta = kick
        game.sendKick(
            zone = kick.zone,
            power = kick.power,
            force = kick.forceN,
            dirDeg = kick.dirDeg,
            height = kick.height,
            spin = kick.spin,
            strike = kick.strike,
            foot = kick.foot,
        )
        binding.big.text = "SHOT ${kick.height}/${kick.strike} ${kick.forceN} N"
        vibrate(120)
        val kickAt = System.currentTimeMillis()
        val kickNo = game.kick
        mainHandler.postDelayed({
            val frames = skelBuf
                .filter { it.first >= kickAt - 850 }
                .map { ((it.first - kickAt).toInt()) to it.second }
            if (frames.isNotEmpty()) {
                val step = max(1, ceil(frames.size / 26.0).toInt())
                game.sendSkeleton(kickNo, frames.filterIndexed { i, _ -> i % step == 0 })
            }
        }, 450)
    }

    override fun onConnected(connected: Boolean) {
        mainHandler.post {
            binding.led.setBackgroundResource(
                if (connected) R.drawable.led_on else R.drawable.led_off
            )
        }
    }

    override fun onState(state: GameClient.MatchState) {
        mainHandler.post {
            if (calibrating) {
                lastPhase = state.phase
                return@post
            }
            pose?.phase = state.phase
            binding.big.setTextColor(ContextCompat.getColor(this, R.color.chalk))
            val holdHint = hintHeldByHost()
            when (state.phase) {
                "lobby" -> {
                    binding.big.text = "READY?"
                    updateProfileHint()
                    lastResultSpoken = null
                }
                "announce" -> {
                    binding.big.text = "KICK ${state.kick} OF ${state.kicksTotal}"
                    if (!holdHint) {
                        binding.hint.text = "Raise a hand to aim — THE WALL is watching…"
                    }
                    if (lastPhase != "announce") {
                        coach?.speak("Kick ${state.kick} of ${state.kicksTotal}. Pick a corner.")
                    }
                }
                "countdown" -> {
                    binding.big.text = ceil(state.timerMs / 1000.0).toInt().toString()
                    if (!holdHint) binding.hint.text = "Hold your fake… switch late!"
                    if (lastPhase != "countdown") {
                        vibrate(30)
                        coach?.speak("Ready")
                    }
                }
                "shoot" -> {
                    binding.big.text = "KICK!"
                    if (!holdHint) {
                        binding.hint.text = "Swing your leg — your hand picks the corner!"
                    }
                    if (lastPhase != "shoot") vibrateBurst()
                }
                "resolve" -> {
                    val r = state.lastResult
                    binding.big.text = when (r) {
                        "goal" -> "GOAL!"
                        "save" -> "SAVED!"
                        "post" -> "POST!"
                        else -> "SKIED!"
                    }
                    binding.big.setTextColor(
                        ContextCompat.getColor(
                            this,
                            if (r == "goal") R.color.amber else R.color.red
                        )
                    )
                    val f = state.lastForce
                    if (!holdHint) {
                        binding.hint.text = buildString {
                            if (f != null && f > 0) append("$f N — ")
                            append(state.line)
                        }
                    }
                    val key = "${state.kick}:$r"
                    if (r != null && key != lastResultSpoken) {
                        lastResultSpoken = key
                        val meta = lastKickMeta
                        qwen?.remember(
                            zone = meta?.zone ?: pose?.zone ?: "C",
                            result = r,
                            forceN = f ?: meta?.forceN ?: 0,
                            height = meta?.height ?: "L",
                            foot = meta?.foot ?: "R",
                        )
                        val line = when (r) {
                            "goal" -> "Goal!"
                            "save" -> "Saved."
                            "post" -> "Off the post."
                            else -> "Missed."
                        }
                        coach?.speak(line)
                        askQwen(r)
                    }
                }
                "end" -> {
                    binding.big.text = "${state.score} / ${state.kicksTotal}"
                    binding.big.setTextColor(ContextCompat.getColor(this, R.color.amber))
                    if (!holdHint) binding.hint.text = state.line
                    if (lastPhase != "end") {
                        coach?.speak("Final ${state.score} of ${state.kicksTotal}.")
                    }
                }
            }
            lastPhase = state.phase
        }
    }

    private fun vibrate(ms: Long) {
        val v = getSystemService(VibratorManager::class.java)?.defaultVibrator
            ?: getSystemService(Vibrator::class.java) ?: return
        v.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE))
    }

    private fun vibrateBurst() {
        val v = getSystemService(VibratorManager::class.java)?.defaultVibrator
            ?: getSystemService(Vibrator::class.java) ?: return
        v.vibrate(VibrationEffect.createWaveform(longArrayOf(0, 70, 40, 70), -1))
    }

    override fun onDestroy() {
        super.onDestroy()
        voice?.close()
        voice = null
        coach?.close()
        coach = null
        qwen?.close()
        qwen = null
        pose?.close()
        game.close()
        cameraExecutor.shutdown()
    }
}
