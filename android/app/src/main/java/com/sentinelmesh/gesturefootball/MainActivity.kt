package com.sentinelmesh.gesturefootball

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Matrix
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.view.View
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
import java.util.concurrent.Executors
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.roundToInt

class MainActivity : AppCompatActivity(), GameClient.Listener {

    private lateinit var binding: ActivityMainBinding
    private lateinit var game: GameClient
    private var pose: PoseAnalyzer? = null
    private val cameraExecutor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())

    private var lastZoneSent = 0L
    private val skelBuf = ArrayDeque<Pair<Long, List<FloatArray>>>()
    private var lastPhase: String? = null

    private var calibrating = false
    private var calib: CalibrationSession? = null
    private var profile: PlayerProfile? = null
    private var pendingCalibKick: ForcePoseEngine.KickEvent? = null

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) startCamera() else binding.hint.text = "Camera permission required"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        game = GameClient(listener = this)
        game.connect()

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
            binding.npuBadge.text = "DELEGATE · ${pose?.delegateLabel}"
        } catch (e: Exception) {
            binding.aiBadge.text = "AI FAILED"
            binding.hint.text = "Model missing or GPU delegate failed: ${e.message}"
        }

        profile = PlayerProfileStore.load(this)
        profile?.let { pose?.applyProfile(it) }
        updateProfileHint()

        binding.calibBtn.setOnClickListener { startCalibration() }
        binding.calibration.calibNext.setOnClickListener { onCalibNext() }
        binding.calibration.calibSkip.setOnClickListener { onCalibSkip() }

        if (profile == null) startCalibration()

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            == PackageManager.PERMISSION_GRANTED
        ) {
            startCamera()
        } else {
            permissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    private fun updateProfileHint() {
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
            vibrate(80)
        }
        calibrating = false
        calib = null
        pendingCalibKick = null
        pose?.calibrationSwing = false
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
        panel.calibBioRow.visibility = if (ui.showBiometrics) View.VISIBLE else View.GONE

        when (ui.step) {
            CalibrationSession.Step.BIOMETRICS -> {
                panel.calibNext.text = getString(R.string.calib_next)
                panel.calibSkip.visibility = View.INVISIBLE
                pose?.calibrationSwing = false
            }
            CalibrationSession.Step.PRACTICE -> {
                pose?.calibrationSwing = true
                // Sensitive so a hard swing always registers; final threshold is 55% of peak.
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
                panel.calibNext.text = "…"
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
        binding.bodyBadge.text = if (hud.bodyOk) "FULL BODY ✓" else "STEP BACK"
        binding.bodyBadge.setTextColor(
            ContextCompat.getColor(this, if (hud.bodyOk) R.color.green else R.color.red)
        )
        binding.forceBadge.text = if (hud.liveForce > 5f)
            "FORCEPOSE · ${hud.liveForce.roundToInt()} N"
        else "FORCEPOSE · — N"
        binding.npuBadge.text = "DELEGATE · ${hud.delegateLabel} · ${hud.latencyMs} ms"

        if (calibrating) {
            val session = calib ?: return
            val kick = pendingCalibKick
            pendingCalibKick = null
            val advanced = session.onPose(
                nowMs = System.currentTimeMillis(),
                bodyOk = hud.bodyOk,
                landmarks = hud.landmarks,
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
        game.sendKick(kick.zone, kick.power, kick.forceN, kick.dirDeg)
        binding.big.text = "SHOT AWAY! ${kick.forceN} N"
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
            when (state.phase) {
                "lobby" -> {
                    binding.big.text = "READY?"
                    updateProfileHint()
                }
                "announce" -> {
                    binding.big.text = "KICK ${state.kick} OF ${state.kicksTotal}"
                    binding.hint.text = "Raise a hand to aim — THE WALL is watching…"
                }
                "countdown" -> {
                    binding.big.text = ceil(state.timerMs / 1000.0).toInt().toString()
                    binding.hint.text = "Hold your fake… switch late!"
                    if (lastPhase != "countdown") vibrate(30)
                }
                "shoot" -> {
                    binding.big.text = "KICK!"
                    binding.hint.text = "Swing your leg — your hand picks the corner!"
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
                    binding.hint.text = buildString {
                        if (f != null && f > 0) append("$f N — ")
                        append(state.line)
                    }
                }
                "end" -> {
                    binding.big.text = "${state.score} / ${state.kicksTotal}"
                    binding.big.setTextColor(ContextCompat.getColor(this, R.color.amber))
                    binding.hint.text = state.line
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
        pose?.close()
        game.close()
        cameraExecutor.shutdown()
    }
}
