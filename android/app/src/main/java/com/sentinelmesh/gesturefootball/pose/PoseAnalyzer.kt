package com.sentinelmesh.gesturefootball.pose

import android.content.Context
import android.graphics.Bitmap
import android.os.SystemClock
import android.util.Log
import com.google.mediapipe.framework.image.BitmapImageBuilder
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.tasks.core.Delegate
import com.google.mediapipe.tasks.vision.core.RunningMode
import com.google.mediapipe.tasks.vision.poselandmarker.PoseLandmarker
import com.google.mediapipe.tasks.vision.poselandmarker.PoseLandmarkerResult
import com.sentinelmesh.gesturefootball.forcepose.ForcePoseEngine
import com.sentinelmesh.gesturefootball.profile.PlayerProfile
import kotlin.math.hypot

/**
 * Pose + ForcePose / aim / kick.
 * Prefers Hexagon NPU (AI Hub QNN ONNX); falls back to MediaPipe GPU/CPU.
 * Tap-cycle: NPU → GPU → CPU → NPU for judge latency demos.
 */
class PoseAnalyzer(
    context: Context,
    private val onHud: (Hud) -> Unit,
    private val onKick: (ForcePoseEngine.KickEvent) -> Unit,
    private val onSkeleton: (Long, List<FloatArray>) -> Unit,
) {
    enum class Mode { NPU, GPU, CPU }

    data class Hud(
        val zone: String,
        val bodyOk: Boolean,
        val liveForce: Float,
        val landmarks: List<FloatArray>?,
        val latencyMs: Long,
        val delegateLabel: String,
        val wristXMirrored: Float? = null,
        val wristY: Float? = null,
        val liveSpeed: Float = 0f,
        val liveFoot: String = "R",
        val bodyOkStreak: Int = 0,
        /** True when pose fills the stand-here guide (used by calibration). */
        val inGuide: Boolean = false,
        /** Mid-shoulder Y (normalized) — calibration requires aim hand above this. */
        val shoulderY: Float? = null,
        /** Why the last near-kick was rejected: soft | short | null. */
        val kickReject: String? = null,
    )

    companion object {
        const val L_SHO = 11
        const val R_SHO = 12
        const val L_WRI = 15
        const val R_WRI = 16
        const val L_HIP = 23
        const val R_HIP = 24
        const val L_ANK = 27
        const val R_ANK = 28
        const val L_FOOT = 31
        const val R_FOOT = 32
        private const val MODEL_ASSET = "pose_landmarker_lite.task"
        private const val TAG = "PoseAnalyzer"
        /** Full-body frames required before a kick can fire (anti-cheat). */
        const val BODY_OK_FRAMES = 8
    }

    private val appContext = context.applicationContext
    private val force = ForcePoseEngine()
    private var landmarkerGpu: PoseLandmarker? = null
    private var landmarkerCpu: PoseLandmarker? = null
    private var npu: NpuPoseEngine? = null
    private var npuAvailable = false
    var mode: Mode = Mode.GPU
        private set
    var zone: String = "C"
        private set
    var phase: String = "lobby"
    var calibrationSwing: Boolean = false
    private var lastKickAt = 0L
    private var lastShootPhase = false
    private var bodyOkStreak = 0
    /** Consecutive NPU null inferences — fall back to MediaPipe so calib isn't stuck. */
    private var npuNullStreak = 0
    /** Last torso midpoint — teleport = person switch / tracker jump. */
    private var lastTorsoX = Float.NaN
    private var lastTorsoY = Float.NaN

    private var aimLMax = 0.34f
    private var aimCMin = 0.40f
    private var aimCMax = 0.60f
    private var aimRMin = 0.66f

    var delegateLabel: String = "GPU"
        private set
    var lastPoseMs: Long = 0
        private set

    init {
        npu = NpuPoseEngine.create(appContext)
        npuAvailable = npu != null
        if (npuAvailable) {
            mode = Mode.NPU
            delegateLabel = "NPU"
        } else {
            mode = Mode.GPU
            ensureLandmarker(Delegate.GPU)
        }
    }

    /** Cycle NPU → GPU → CPU → NPU (skips NPU if unavailable). */
    fun cycleDelegate(): String {
        mode = when (mode) {
            Mode.NPU -> Mode.GPU
            Mode.GPU -> Mode.CPU
            Mode.CPU -> if (npuAvailable) Mode.NPU else Mode.GPU
        }
        when (mode) {
            Mode.NPU -> {
                if (npu == null) npu = NpuPoseEngine.create(appContext)
                npuAvailable = npu != null
                if (!npuAvailable) {
                    mode = Mode.GPU
                    ensureLandmarker(Delegate.GPU)
                } else {
                    npuNullStreak = 0
                    delegateLabel = "NPU"
                }
            }
            Mode.GPU -> ensureLandmarker(Delegate.GPU)
            Mode.CPU -> ensureLandmarker(Delegate.CPU)
        }
        Log.i(TAG, "delegate → $delegateLabel")
        return delegateLabel
    }

    private fun ensureLandmarker(delegate: Delegate): Boolean {
        val slot = if (delegate == Delegate.CPU) ::landmarkerCpu else ::landmarkerGpu
        if (slot.get() != null) {
            delegateLabel = if (delegate == Delegate.CPU) "CPU" else "GPU"
            return true
        }
        return try {
            val base = BaseOptions.builder()
                .setModelAssetPath(MODEL_ASSET)
                .setDelegate(delegate)
                .build()
            val options = PoseLandmarker.PoseLandmarkerOptions.builder()
                .setBaseOptions(base)
                .setRunningMode(RunningMode.VIDEO)
                .setNumPoses(1)
                .build()
            val lm = PoseLandmarker.createFromOptions(appContext, options)
            if (delegate == Delegate.CPU) landmarkerCpu = lm else landmarkerGpu = lm
            delegateLabel = if (delegate == Delegate.CPU) "CPU" else "GPU"
            true
        } catch (e: Exception) {
            e.printStackTrace()
            false
        }
    }

    fun applyProfile(profile: PlayerProfile) {
        force.applyProfile(profile)
        aimLMax = profile.aimLMax
        aimCMin = profile.aimCMin
        aimCMax = profile.aimCMax
        aimRMin = profile.aimRMin
    }

    fun setKickThreshold(ms: Float) {
        force.setKickThreshold(ms)
    }

    fun forceZone(z: String) {
        if (z == "L" || z == "C" || z == "R") zone = z
    }

    fun close() {
        npu?.close()
        npu = null
        landmarkerGpu?.close()
        landmarkerGpu = null
        landmarkerCpu?.close()
        landmarkerCpu = null
    }

    fun analyze(bitmap: Bitmap, timestampMs: Long) {
        when (mode) {
            Mode.NPU -> {
                val engine = npu
                if (engine != null) analyzeNpu(engine, bitmap, timestampMs)
                else {
                    mode = Mode.GPU
                    ensureLandmarker(Delegate.GPU)
                    analyzeMp(bitmap, timestampMs, landmarkerGpu, "GPU")
                }
            }
            Mode.GPU -> analyzeMp(bitmap, timestampMs, landmarkerGpu, "GPU")
            Mode.CPU -> analyzeMp(bitmap, timestampMs, landmarkerCpu, "CPU")
        }
    }

    private fun analyzeNpu(engine: NpuPoseEngine, bitmap: Bitmap, timestampMs: Long) {
        val result = try {
            engine.infer(bitmap)
        } catch (e: Exception) {
            Log.e(TAG, "NPU infer failed", e)
            npu?.close()
            npu = null
            npuAvailable = false
            mode = Mode.GPU
            if (ensureLandmarker(Delegate.GPU)) {
                analyzeMp(bitmap, timestampMs, landmarkerGpu, "GPU")
            } else {
                onHud(Hud(zone, false, 0f, null, 0, "FAIL"))
            }
            return
        }
        if (result == null) {
            npuNullStreak++
            // MediaPipe full-frame pose is reliable for calibration; use it whenever
            // NPU can't see a body this frame. Sticky-switch after sustained misses.
            if (ensureLandmarker(Delegate.GPU)) {
                analyzeMp(bitmap, timestampMs, landmarkerGpu, "GPU")
                if (npuNullStreak >= 120) {
                    Log.w(TAG, "NPU miss streak=$npuNullStreak — sticking to GPU")
                    mode = Mode.GPU
                    delegateLabel = "GPU"
                }
            } else {
                onHud(Hud(zone, false, 0f, null, 0, "NPU", bodyOkStreak = bodyOkStreak))
            }
            return
        }
        npuNullStreak = 0
        processLandmarks(
            landmarks = result.landmarks33,
            vis = { 1f },
            world = result.landmarks33.map { floatArrayOf(it[0], it[1], 0f) },
            latency = result.latencyMs,
            timestampMs = timestampMs,
            label = "NPU",
        )
    }

    private fun analyzeMp(
        bitmap: Bitmap,
        timestampMs: Long,
        lm: PoseLandmarker?,
        label: String,
    ) {
        val landmarker = lm ?: run {
            ensureLandmarker(if (label == "CPU") Delegate.CPU else Delegate.GPU)
            if (label == "CPU") landmarkerCpu else landmarkerGpu
        }
        if (landmarker == null) {
            onHud(Hud(zone, false, 0f, null, 0, label, bodyOkStreak = bodyOkStreak))
            return
        }
        val t0 = SystemClock.elapsedRealtime()
        val mpImage = BitmapImageBuilder(bitmap).build()
        val result: PoseLandmarkerResult = landmarker.detectForVideo(mpImage, timestampMs)
        val latency = SystemClock.elapsedRealtime() - t0
        val landmarks = result.landmarks().firstOrNull()
        val world = result.worldLandmarks().firstOrNull()
        if (landmarks == null) {
            bodyOkStreak = 0
            lastPoseMs = latency
            onHud(Hud(zone, false, 0f, null, latency, label, bodyOkStreak = 0))
            return
        }
        val pts2d = landmarks.map { floatArrayOf(it.x(), it.y()) }
        processLandmarks(
            landmarks = pts2d,
            vis = { i -> landmarks[i].visibility().orElse(1f) },
            world = world?.map { floatArrayOf(it.x(), it.y(), it.z()) },
            latency = latency,
            timestampMs = timestampMs,
            label = label,
        )
    }

    private fun processLandmarks(
        landmarks: List<FloatArray>,
        vis: (Int) -> Float,
        world: List<FloatArray>?,
        latency: Long,
        timestampMs: Long,
        label: String,
    ) {
        fun x(i: Int) = landmarks[i][0]
        fun y(i: Int) = landmarks[i][1]

        val torsoOk = BodyGuide.hasTorso(landmarks, vis)
        val bodyOk = torsoOk && (
            (vis(L_ANK) > 0.30f || vis(L_FOOT) > 0.30f) &&
                (vis(R_ANK) > 0.30f || vis(R_FOOT) > 0.30f) ||
                // NPU synthesises feet at vis=1; treat torso+guide as full body too.
                BodyGuide.contains(landmarks, vis)
            )
        val inGuide = BodyGuide.contains(landmarks, vis)

        val shoulderMidX = (x(L_SHO) + x(R_SHO)) / 2f
        val shoulderMidY = (y(L_SHO) + y(R_SHO)) / 2f
        val hipMidX = (x(L_HIP) + x(R_HIP)) / 2f
        val hipMidY = (y(L_HIP) + y(R_HIP)) / 2f
        val torsoX = (shoulderMidX + hipMidX) / 2f
        val torsoY = (shoulderMidY + hipMidY) / 2f
        // Second person / tracker jump: clear foot history so we don't invent kicks.
        if (!lastTorsoX.isNaN()) {
            val jump = hypot(torsoX - lastTorsoX, torsoY - lastTorsoY)
            if (jump > 0.15f) {
                force.resetBuffers()
                bodyOkStreak = 0
                Log.i(TAG, "person-switch jump=$jump — buffers cleared")
            }
        }
        lastTorsoX = torsoX
        lastTorsoY = torsoY

        bodyOkStreak = if (bodyOk || inGuide || torsoOk) bodyOkStreak + 1 else 0
        lastPoseMs = latency
        delegateLabel = label

        val hipY = hipMidY
        val wrists = listOf(L_WRI, R_WRI)
            .filter { y(it) < hipY && vis(it) > 0.4f }
            .minByOrNull { y(it) }
        var wristXMirrored: Float? = null
        var wristY: Float? = null
        if (wrists != null) {
            val wx = 1f - x(wrists)
            wristXMirrored = wx
            wristY = y(wrists)
            zone = when {
                zone != "L" && wx < aimLMax -> "L"
                zone != "R" && wx > aimRMin -> "R"
                zone != "C" && wx > aimCMin && wx < aimCMax -> "C"
                else -> zone
            }
        }

        val shoot = phase == "shoot"
        if ((shoot || calibrationSwing) && !lastShootPhase) force.resetSwing()
        lastShootPhase = shoot || calibrationSwing

        val lfX = (x(L_ANK) + x(L_FOOT)) / 2f
        val lfY = (y(L_ANK) + y(L_FOOT)) / 2f
        val rfX = (x(R_ANK) + x(R_FOOT)) / 2f
        val rfY = (y(R_ANK) + y(R_FOOT)) / 2f
        val framedOk = calibrationSwing || bodyOkStreak >= BODY_OK_FRAMES
        val canKick = (shoot || calibrationSwing) && framedOk && timestampMs - lastKickAt > 900

        val kick = force.update(
            nowMs = timestampMs,
            leftFootX = lfX, leftFootY = lfY, leftVis = vis(L_ANK),
            rightFootX = rfX, rightFootY = rfY, rightVis = vis(R_ANK),
            shoulderMidX = shoulderMidX,
            shoulderMidY = shoulderMidY,
            hipMidX = hipMidX,
            hipMidY = hipMidY,
            zone = zone,
            canKick = canKick,
            aimHandY = wristY,
            leftWristX = x(L_WRI), leftWristY = y(L_WRI), leftWristVis = vis(L_WRI),
            rightWristX = x(R_WRI), rightWristY = y(R_WRI), rightWristVis = vis(R_WRI),
        )
        if (kick != null) {
            lastKickAt = timestampMs
            onKick(kick)
        }

        if (world != null) {
            onSkeleton(timestampMs, world)
        }

        onHud(
            Hud(
                zone, bodyOk, force.liveForce, landmarks, latency, label,
                wristXMirrored = wristXMirrored,
                wristY = wristY,
                liveSpeed = force.liveSpeed,
                liveFoot = force.liveFoot,
                bodyOkStreak = bodyOkStreak,
                inGuide = inGuide,
                shoulderY = shoulderMidY,
                kickReject = force.consumeReject(),
            )
        )
    }
}
