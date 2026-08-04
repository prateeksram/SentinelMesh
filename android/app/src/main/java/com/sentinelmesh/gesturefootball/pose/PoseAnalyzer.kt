package com.sentinelmesh.gesturefootball.pose

import android.content.Context
import android.graphics.Bitmap
import android.os.SystemClock
import com.google.mediapipe.framework.image.BitmapImageBuilder
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.tasks.core.Delegate
import com.google.mediapipe.tasks.vision.core.RunningMode
import com.google.mediapipe.tasks.vision.poselandmarker.PoseLandmarker
import com.google.mediapipe.tasks.vision.poselandmarker.PoseLandmarkerResult
import com.sentinelmesh.gesturefootball.forcepose.ForcePoseEngine
import com.sentinelmesh.gesturefootball.profile.PlayerProfile

/**
 * Pose + ForcePose / aim / kick.
 * Prefers Hexagon NPU (AI Hub QNN ONNX); falls back to MediaPipe GPU.
 */
class PoseAnalyzer(
    context: Context,
    private val onHud: (Hud) -> Unit,
    private val onKick: (ForcePoseEngine.KickEvent) -> Unit,
    private val onSkeleton: (Long, List<FloatArray>) -> Unit,
) {
    data class Hud(
        val zone: String,
        val bodyOk: Boolean,
        val liveForce: Float,
        val landmarks: List<FloatArray>?,
        val latencyMs: Long,
        val delegateLabel: String,
        val wristXMirrored: Float? = null,
        val liveSpeed: Float = 0f,
        val liveFoot: String = "R",
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
    }

    private val appContext = context.applicationContext
    private val force = ForcePoseEngine()
    private var landmarker: PoseLandmarker? = null
    private var npu: NpuPoseEngine? = null
    var zone: String = "C"
        private set
    var phase: String = "lobby"
    var calibrationSwing: Boolean = false
    private var lastKickAt = 0L
    private var lastShootPhase = false

    private var aimLMax = 0.34f
    private var aimCMin = 0.40f
    private var aimCMax = 0.60f
    private var aimRMin = 0.66f

    var delegateLabel: String = "GPU"
        private set

    init {
        npu = NpuPoseEngine.create(appContext)
        if (npu != null) {
            delegateLabel = "NPU"
        } else {
            ensureGpu()
        }
    }

    private fun ensureGpu(): Boolean {
        if (landmarker != null) {
            delegateLabel = "GPU"
            return true
        }
        return try {
            val base = BaseOptions.builder()
                .setModelAssetPath(MODEL_ASSET)
                .setDelegate(Delegate.GPU)
                .build()
            val options = PoseLandmarker.PoseLandmarkerOptions.builder()
                .setBaseOptions(base)
                .setRunningMode(RunningMode.VIDEO)
                .setNumPoses(1)
                .build()
            landmarker = PoseLandmarker.createFromOptions(appContext, options)
            delegateLabel = "GPU"
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

    fun close() {
        npu?.close()
        npu = null
        landmarker?.close()
        landmarker = null
    }

    fun analyze(bitmap: Bitmap, timestampMs: Long) {
        val npuEngine = npu
        if (npuEngine != null) {
            analyzeNpu(npuEngine, bitmap, timestampMs)
        } else {
            analyzeGpu(bitmap, timestampMs)
        }
    }

    private fun analyzeNpu(engine: NpuPoseEngine, bitmap: Bitmap, timestampMs: Long) {
        val result = try {
            engine.infer(bitmap)
        } catch (e: Exception) {
            e.printStackTrace()
            npu?.close()
            npu = null
            if (ensureGpu()) {
                analyzeGpu(bitmap, timestampMs)
            } else {
                onHud(Hud(zone, false, 0f, null, 0, "FAIL"))
            }
            return
        }
        if (result == null) {
            onHud(Hud(zone, false, 0f, null, 0, "NPU"))
            return
        }
        processLandmarks(
            landmarks = result.landmarks33,
            vis = { 1f },
            world = result.landmarks33.map { floatArrayOf(it[0], it[1], 0f) },
            latency = result.latencyMs,
            timestampMs = timestampMs,
            label = "NPU",
        )
    }

    private fun analyzeGpu(bitmap: Bitmap, timestampMs: Long) {
        val lm = landmarker ?: run {
            onHud(Hud(zone, false, 0f, null, 0, delegateLabel))
            return
        }
        val t0 = SystemClock.elapsedRealtime()
        val mpImage = BitmapImageBuilder(bitmap).build()
        val result: PoseLandmarkerResult = lm.detectForVideo(mpImage, timestampMs)
        val latency = SystemClock.elapsedRealtime() - t0
        val landmarks = result.landmarks().firstOrNull()
        val world = result.worldLandmarks().firstOrNull()
        if (landmarks == null) {
            onHud(Hud(zone, false, 0f, null, latency, delegateLabel))
            return
        }
        val pts2d = landmarks.map { floatArrayOf(it.x(), it.y()) }
        processLandmarks(
            landmarks = pts2d,
            vis = { i -> landmarks[i].visibility().orElse(1f) },
            world = world?.map { floatArrayOf(it.x(), it.y(), it.z()) },
            latency = latency,
            timestampMs = timestampMs,
            label = delegateLabel,
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

        val bodyOk = vis(L_SHO) > 0.5f && vis(R_SHO) > 0.5f &&
            vis(L_ANK) > 0.5f && vis(R_ANK) > 0.5f

        val hipY = (y(L_HIP) + y(R_HIP)) / 2f
        val wrists = listOf(L_WRI, R_WRI)
            .filter { y(it) < hipY && vis(it) > 0.4f }
            .minByOrNull { y(it) }
        var wristXMirrored: Float? = null
        if (wrists != null) {
            val wx = 1f - x(wrists)
            wristXMirrored = wx
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
        val canKick = (shoot || calibrationSwing) && bodyOk && timestampMs - lastKickAt > 900

        val kick = force.update(
            nowMs = timestampMs,
            leftFootX = lfX, leftFootY = lfY, leftVis = vis(L_ANK),
            rightFootX = rfX, rightFootY = rfY, rightVis = vis(R_ANK),
            shoulderMidX = (x(L_SHO) + x(R_SHO)) / 2f,
            shoulderMidY = (y(L_SHO) + y(R_SHO)) / 2f,
            hipMidX = (x(L_HIP) + x(R_HIP)) / 2f,
            hipMidY = (y(L_HIP) + y(R_HIP)) / 2f,
            zone = zone,
            canKick = canKick,
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
                liveSpeed = force.liveSpeed,
                liveFoot = force.liveFoot,
            )
        )
    }
}
