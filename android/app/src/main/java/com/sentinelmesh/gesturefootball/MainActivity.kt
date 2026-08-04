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
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import com.sentinelmesh.gesturefootball.databinding.ActivityMainBinding
import com.sentinelmesh.gesturefootball.forcepose.ForcePoseEngine
import com.sentinelmesh.gesturefootball.net.GameClient
import com.sentinelmesh.gesturefootball.pose.PoseAnalyzer
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

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            == PackageManager.PERMISSION_GRANTED
        ) {
            startCamera()
        } else {
            permissionLauncher.launch(Manifest.permission.CAMERA)
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
            pose?.phase = state.phase
            binding.big.setTextColor(ContextCompat.getColor(this, R.color.chalk))
            when (state.phase) {
                "lobby" -> {
                    binding.big.text = "READY?"
                    binding.hint.text = "Start the match on the TV."
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
