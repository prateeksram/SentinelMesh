package com.sentinelmesh.gesturefootball.pose

import com.sentinelmesh.gesturefootball.forcepose.ForcePoseEngine
import com.sentinelmesh.gesturefootball.profile.PlayerProfile
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.acos
import kotlin.math.atan2
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sqrt

/**
 * Low-frame-rate kick detector used only by the UNO Q pose source.
 *
 * Unlike the legacy phone ForcePose path, this engine is time based. A
 * pelvis-relative foot track advances through rest -> wind-up -> forward ->
 * follow-through and accepts one high-confidence speed peak plus a real path.
 * A time-aware One Euro filter replaces the fixed median + Savitzky-Golay
 * sample windows, whose latency changes dramatically with source FPS.
 */
class EdgeKickEngine(
    bodyKg: Float = 70f,
    kickMs: Float = 3.0f,
    torsoM: Float = 0.52f,
    private val fMax: Float = 380f,
) {
    enum class SwingState { REST, WINDUP, FORWARD, FOLLOW_THROUGH, COOLDOWN }

    data class FlowFoot(
        val vxNorm: Float = 0f,
        val vyNorm: Float = 0f,
        val peakVxNorm: Float = 0f,
        val peakVyNorm: Float = 0f,
        val dxNorm: Float = 0f,
        val dyNorm: Float = 0f,
        val confidence: Float = 0f,
        val samples: Int = 0,
    )

    data class FlowMotion(
        val timestampNs: Long = 0L,
        val fps: Float = 0f,
        val left: FlowFoot? = null,
        val right: FlowFoot? = null,
    )

    data class Diagnostics(
        val frameDeltaMs: Long = 0L,
        val bufferFill: Int = 0,
        val foot: String = "R",
        val swingState: SwingState = SwingState.REST,
        val rawSpeed: Float = 0f,
        val filteredSpeed: Float = 0f,
        val signalSpeed: Float = 0f,
        val threshold: Float = 0f,
        val aboveThresholdMs: Long = 0L,
        val aboveThresholdSamples: Int = 0,
        val displacementM: Float = 0f,
        val liftM: Float = 0f,
        val kneeExtensionDeg: Float = 0f,
        val ankleVisibility: Float = 0f,
        val heelVisibility: Float = 0f,
        val footVisibility: Float = 0f,
        val flowFps: Float = 0f,
        val flowConfidence: Float = 0f,
        val flowSamples: Int = 0,
        val reject: String? = null,
    )

    data class Result(
        val kick: ForcePoseEngine.KickEvent?,
        val diagnostics: Diagnostics,
    )

    private data class Point(val x: Float, val y: Float)
    private data class FilteredPoint(
        val x: Float,
        val y: Float,
        val vx: Float,
        val vy: Float,
    )

    private data class MotionSample(
        val timeMs: Long,
        val position: Point,
        val signalSpeed: Float,
    )

    private data class Candidate(
        val event: ForcePoseEngine.KickEvent,
        val score: Float,
    )

    private class LowPass {
        private var initialized = false
        private var value = 0f

        fun filter(input: Float, alpha: Float): Float {
            value = if (!initialized) input else alpha * input + (1f - alpha) * value
            initialized = true
            return value
        }

        fun reset() {
            initialized = false
            value = 0f
        }
    }

    private class OneEuro2D(
        private val minCutoff: Float = 1.4f,
        private val beta: Float = 0.055f,
        private val derivativeCutoff: Float = 1.0f,
    ) {
        private val xFilter = LowPass()
        private val yFilter = LowPass()
        private val dxFilter = LowPass()
        private val dyFilter = LowPass()
        private var initialized = false
        private var previousRaw = Point(0f, 0f)
        private var previousFiltered = Point(0f, 0f)

        fun filter(point: Point, dt: Float): FilteredPoint {
            if (!initialized || dt <= 0f || dt > 0.55f) {
                reset()
                initialized = true
                previousRaw = point
                previousFiltered = point
                return FilteredPoint(point.x, point.y, 0f, 0f)
            }

            val rawDx = (point.x - previousRaw.x) / dt
            val rawDy = (point.y - previousRaw.y) / dt
            val derivativeAlpha = alpha(derivativeCutoff, dt)
            val dx = dxFilter.filter(rawDx, derivativeAlpha)
            val dy = dyFilter.filter(rawDy, derivativeAlpha)
            val cutoff = minCutoff + beta * hypot(dx, dy)
            val positionAlpha = alpha(cutoff, dt)
            val x = xFilter.filter(point.x, positionAlpha)
            val y = yFilter.filter(point.y, positionAlpha)
            val vx = (x - previousFiltered.x) / dt
            val vy = (y - previousFiltered.y) / dt
            previousRaw = point
            previousFiltered = Point(x, y)
            return FilteredPoint(x, y, vx, vy)
        }

        fun reset() {
            initialized = false
            xFilter.reset()
            yFilter.reset()
            dxFilter.reset()
            dyFilter.reset()
        }

        private fun alpha(cutoff: Float, dt: Float): Float {
            val tau = 1f / (2f * PI.toFloat() * cutoff.coerceAtLeast(0.01f))
            return 1f / (1f + tau / dt.coerceAtLeast(0.001f))
        }
    }

    private class Track(val side: String) {
        val filter = OneEuro2D()
        val samples = ArrayDeque<MotionSample>()
        var state = SwingState.REST
        var previousTimeMs = 0L
        var previousRaw: Point? = null
        var rest = Point(0f, 0f)
        var restReady = false
        var swingStart = Point(0f, 0f)
        var swingStartedAt = 0L
        var forwardStartedAt = 0L
        var cooldownUntil = 0L
        var peakSpeed = 0f
        var peakVx = 0f
        var peakVy = 0f
        var peakForce = 0f
        var previousSignalSpeed = 0f
        var maxDisplacement = 0f
        var maxLift = 0f
        var minKneeAngle = 180f
        var kneeExtension = 0f
        var aboveStartedAt = 0L
        var aboveThresholdMs = 0L
        var aboveThresholdSamples = 0
        var lastReject: String? = null
        var lastRawSpeed = 0f
        var lastFilteredSpeed = 0f
        var lastSignalSpeed = 0f
        var lastAnkleVis = 0f
        var lastHeelVis = 0f
        var lastFootVis = 0f
        var lastFlowConfidence = 0f
        var lastFlowSamples = 0

        fun clear() {
            filter.reset()
            samples.clear()
            state = SwingState.REST
            previousTimeMs = 0L
            previousRaw = null
            restReady = false
            swingStartedAt = 0L
            forwardStartedAt = 0L
            cooldownUntil = 0L
            resetSwingMetrics()
            lastReject = null
        }

        fun resetSwingMetrics() {
            peakSpeed = 0f
            peakVx = 0f
            peakVy = 0f
            peakForce = 0f
            previousSignalSpeed = 0f
            maxDisplacement = 0f
            maxLift = 0f
            minKneeAngle = 180f
            kneeExtension = 0f
            aboveStartedAt = 0L
            aboveThresholdMs = 0L
            aboveThresholdSamples = 0
        }
    }

    private var legKg = 0.0618f * bodyKg
    private var torsoM = torsoM
    private var kickMs = max(ForcePoseEngine.FLOOR_MS, kickMs)
    private var torsoEma = 0f
    private val left = Track("L")
    private val right = Track("R")

    var liveForce = 0f
        private set
    var liveSpeed = 0f
        private set
    var liveFoot = "R"
        private set

    fun applyProfile(profile: PlayerProfile) {
        legKg = 0.0618f * profile.weightKg
        torsoM = profile.torsoM
        kickMs = max(ForcePoseEngine.FLOOR_MS, profile.unoQKickMs ?: profile.kickMs)
    }

    fun setKickThreshold(ms: Float) {
        kickMs = max(ForcePoseEngine.PRACTICE_FLOOR_MS, ms)
    }

    fun reset() {
        left.clear()
        right.clear()
        torsoEma = 0f
        liveForce = 0f
        liveSpeed = 0f
    }

    /** Start a new allowed swing window without discarding the stable rest/filter state. */
    fun resetSwing() {
        for (track in listOf(left, right)) {
            if (track.state != SwingState.COOLDOWN) track.state = SwingState.REST
            track.resetSwingMetrics()
            track.lastReject = null
        }
        liveForce = 0f
    }

    fun update(
        nowMs: Long,
        landmarks: List<FloatArray>,
        visibility: FloatArray,
        frameWidth: Int,
        frameHeight: Int,
        zone: String,
        canKick: Boolean,
        gateReject: String?,
        aimHandY: Float?,
        flow: FlowMotion?,
    ): Result {
        if (landmarks.size != 33 || visibility.size != 33) {
            reset()
            return Result(null, Diagnostics(threshold = kickMs, reject = "no pose"))
        }

        val aspect = frameWidth.coerceAtLeast(1).toFloat() / frameHeight.coerceAtLeast(1)
        fun point(index: Int) = Point(landmarks[index][0] * aspect, landmarks[index][1])
        val shoulderMid = midpoint(point(PoseAnalyzer.L_SHO), point(PoseAnalyzer.R_SHO))
        val hipLeft = point(PoseAnalyzer.L_HIP)
        val hipRight = point(PoseAnalyzer.R_HIP)
        val hipMid = midpoint(hipLeft, hipRight)
        val torsoLength = distance(shoulderMid, hipMid)
        if (torsoLength < 0.04f) {
            reset()
            return Result(null, Diagnostics(threshold = kickMs, reject = "no torso"))
        }
        torsoEma = if (torsoEma <= 0f) torsoLength else 0.92f * torsoEma + 0.08f * torsoLength
        val metresPerUnit = torsoM / torsoEma.coerceAtLeast(0.04f)

        val candidates = ArrayList<Candidate>(2)
        updateTrack(
            track = left,
            nowMs = nowMs,
            landmarks = landmarks,
            visibility = visibility,
            aspect = aspect,
            pelvis = hipMid,
            metresPerUnit = metresPerUnit,
            zone = zone,
            canKick = canKick,
            gateReject = gateReject,
            aimHandY = aimHandY,
            flowFoot = flow?.left,
        )?.also { candidates += it }
        updateTrack(
            track = right,
            nowMs = nowMs,
            landmarks = landmarks,
            visibility = visibility,
            aspect = aspect,
            pelvis = hipMid,
            metresPerUnit = metresPerUnit,
            zone = zone,
            canKick = canKick,
            gateReject = gateReject,
            aimHandY = aimHandY,
            flowFoot = flow?.right,
        )?.also { candidates += it }

        val winner = candidates.maxByOrNull { it.score }
        if (winner != null) {
            for (track in listOf(left, right)) {
                track.state = SwingState.COOLDOWN
                track.cooldownUntil = nowMs + COOLDOWN_MS
                track.lastReject = null
            }
        }

        val active = if (left.lastSignalSpeed >= right.lastSignalSpeed) left else right
        liveSpeed = active.lastSignalSpeed
        liveFoot = active.side
        liveForce = max(left.peakForce, right.peakForce).let { if (liveSpeed < 0.45f) 0f else it }
        val frameDelta = active.previousTimeMs.takeIf { it > 0L }?.let {
            // previousTimeMs has already advanced, so the latest sample carries the real delta.
            active.samples.lastOrNull()?.let { newest ->
                active.samples.dropLast(1).lastOrNull()?.let { prior -> newest.timeMs - prior.timeMs }
            }
        } ?: 0L
        val diagnostics = Diagnostics(
            frameDeltaMs = frameDelta,
            bufferFill = active.samples.size,
            foot = active.side,
            swingState = active.state,
            rawSpeed = active.lastRawSpeed,
            filteredSpeed = active.lastFilteredSpeed,
            signalSpeed = active.lastSignalSpeed,
            threshold = kickMs,
            aboveThresholdMs = active.aboveThresholdMs,
            aboveThresholdSamples = active.aboveThresholdSamples,
            displacementM = active.maxDisplacement,
            liftM = active.maxLift,
            kneeExtensionDeg = active.kneeExtension,
            ankleVisibility = active.lastAnkleVis,
            heelVisibility = active.lastHeelVis,
            footVisibility = active.lastFootVis,
            flowFps = flow?.fps ?: 0f,
            flowConfidence = active.lastFlowConfidence,
            flowSamples = active.lastFlowSamples,
            reject = active.lastReject ?: gateReject,
        )
        return Result(winner?.event, diagnostics)
    }

    private fun updateTrack(
        track: Track,
        nowMs: Long,
        landmarks: List<FloatArray>,
        visibility: FloatArray,
        aspect: Float,
        pelvis: Point,
        metresPerUnit: Float,
        zone: String,
        canKick: Boolean,
        gateReject: String?,
        aimHandY: Float?,
        flowFoot: FlowFoot?,
    ): Candidate? {
        val isLeft = track.side == "L"
        val hipIndex = if (isLeft) PoseAnalyzer.L_HIP else PoseAnalyzer.R_HIP
        val kneeIndex = if (isLeft) PoseAnalyzer.L_KNEE else PoseAnalyzer.R_KNEE
        val ankleIndex = if (isLeft) PoseAnalyzer.L_ANK else PoseAnalyzer.R_ANK
        val heelIndex = if (isLeft) PoseAnalyzer.L_HEEL else PoseAnalyzer.R_HEEL
        val footIndex = if (isLeft) PoseAnalyzer.L_FOOT else PoseAnalyzer.R_FOOT

        track.lastAnkleVis = visibility[ankleIndex]
        track.lastHeelVis = visibility[heelIndex]
        track.lastFootVis = visibility[footIndex]
        val foot = weightedFoot(landmarks, visibility, aspect, ankleIndex, heelIndex, footIndex)
        if (foot == null) {
            track.lastReject = "foot hidden"
            track.lastSignalSpeed = 0f
            return null
        }

        val relativeMetres = Point(
            (foot.x - pelvis.x) * metresPerUnit,
            (foot.y - pelvis.y) * metresPerUnit,
        )
        val dt = if (track.previousTimeMs > 0L) {
            ((nowMs - track.previousTimeMs) / 1000f).coerceIn(0f, 0.55f)
        } else {
            0f
        }
        val previousRaw = track.previousRaw
        val rawVx = if (previousRaw != null && dt > 0f) (relativeMetres.x - previousRaw.x) / dt else 0f
        val rawVy = if (previousRaw != null && dt > 0f) (relativeMetres.y - previousRaw.y) / dt else 0f
        val rawSpeed = hypot(rawVx, rawVy)
        val filtered = track.filter.filter(relativeMetres, dt)
        var filteredVx = filtered.vx
        var filteredVy = filtered.vy
        var filteredSpeed = hypot(filteredVx, filteredVy)

        val usableFlow = flowFoot?.takeIf { it.samples >= 2 && it.confidence >= MIN_FLOW_CONFIDENCE }
        val flowVx = (usableFlow?.vxNorm ?: 0f) * aspect * metresPerUnit
        val flowVy = (usableFlow?.vyNorm ?: 0f) * metresPerUnit
        val flowPeakVx = (usableFlow?.peakVxNorm ?: 0f) * aspect * metresPerUnit
        val flowPeakVy = (usableFlow?.peakVyNorm ?: 0f) * metresPerUnit
        val flowSpeed = hypot(flowVx, flowVy)
        val flowPeakSpeed = hypot(flowPeakVx, flowPeakVy)
        val flowDisplacement = hypot(
            (usableFlow?.dxNorm ?: 0f) * aspect * metresPerUnit,
            (usableFlow?.dyNorm ?: 0f) * metresPerUnit,
        )
        if (flowSpeed > filteredSpeed) {
            filteredVx = flowVx
            filteredVy = flowVy
            filteredSpeed = flowSpeed
        }
        val signalSpeed = max(max(rawSpeed, filteredSpeed), flowPeakSpeed)
        val signalVx = if (flowPeakSpeed > hypot(filteredVx, filteredVy)) flowPeakVx else filteredVx
        val signalVy = if (flowPeakSpeed > hypot(filteredVx, filteredVy)) flowPeakVy else filteredVy

        val acceleration = if (dt > 0f) abs(signalSpeed - track.previousSignalSpeed) / dt else 0f
        val force = min(fMax * 1.5f, legKg * acceleration)
        val current = Point(filtered.x, filtered.y)
        if (!track.restReady) {
            track.rest = current
            track.restReady = true
        }

        track.previousTimeMs = nowMs
        track.previousRaw = relativeMetres
        track.previousSignalSpeed = signalSpeed
        track.lastRawSpeed = rawSpeed
        track.lastFilteredSpeed = hypot(filtered.vx, filtered.vy)
        track.lastSignalSpeed = signalSpeed
        track.lastFlowConfidence = usableFlow?.confidence ?: 0f
        track.lastFlowSamples = usableFlow?.samples ?: 0
        track.samples.addLast(MotionSample(nowMs, current, signalSpeed))
        while (track.samples.isNotEmpty() && nowMs - track.samples.first().timeMs > HISTORY_MS) {
            track.samples.removeFirst()
        }

        if (track.state == SwingState.COOLDOWN) {
            if (nowMs >= track.cooldownUntil) {
                track.state = SwingState.REST
                track.resetSwingMetrics()
            } else {
                track.lastReject = "cooldown"
                return null
            }
        }

        val movementFromRest = distance(current, track.rest)
        if (track.state == SwingState.REST && signalSpeed < REST_SPEED_MPS) {
            track.rest = Point(
                0.92f * track.rest.x + 0.08f * current.x,
                0.92f * track.rest.y + 0.08f * current.y,
            )
        }

        val kneeAngle = kneeAngle(
            hip = rawPoint(landmarks, hipIndex, aspect),
            knee = rawPoint(landmarks, kneeIndex, aspect),
            ankle = rawPoint(landmarks, ankleIndex, aspect),
            visible = min(visibility[hipIndex], min(visibility[kneeIndex], visibility[ankleIndex])) > 0.25f,
        )

        when (track.state) {
            SwingState.REST -> {
                if (movementFromRest >= WINDUP_DISPLACEMENT_M || signalSpeed >= kickMs * START_SPEED_RATIO) {
                    track.state = SwingState.WINDUP
                    track.swingStart = track.rest
                    track.swingStartedAt = nowMs
                    track.resetSwingMetrics()
                }
            }
            SwingState.WINDUP -> {
                if (signalSpeed >= kickMs * FORWARD_SPEED_RATIO) {
                    track.state = SwingState.FORWARD
                    track.forwardStartedAt = nowMs
                } else if (nowMs - track.swingStartedAt > WINDUP_TIMEOUT_MS) {
                    track.lastReject = if (track.peakSpeed > kickMs * 0.55f) "soft" else "no speed"
                    track.state = SwingState.REST
                }
            }
            SwingState.FORWARD -> {
                val forwardAge = nowMs - track.forwardStartedAt
                if (
                    track.peakSpeed >= kickMs &&
                    (signalSpeed <= track.peakSpeed * PEAK_DROP_RATIO || forwardAge >= PEAK_WAIT_MS)
                ) {
                    track.state = SwingState.FOLLOW_THROUGH
                } else if (nowMs - track.swingStartedAt > SWING_TIMEOUT_MS) {
                    track.lastReject = if (track.peakSpeed >= kickMs) "short" else "soft"
                    track.state = SwingState.REST
                }
            }
            SwingState.FOLLOW_THROUGH -> Unit
            SwingState.COOLDOWN -> Unit
        }

        if (track.state != SwingState.REST) {
            track.peakForce = max(track.peakForce, force)
            if (signalSpeed >= track.peakSpeed) {
                track.peakSpeed = signalSpeed
                track.peakVx = signalVx
                track.peakVy = signalVy
            }
            track.maxDisplacement = max(
                track.maxDisplacement,
                max(distance(current, track.swingStart), flowDisplacement),
            )
            track.maxLift = max(track.maxLift, track.rest.y - current.y)
            if (kneeAngle != null) {
                track.minKneeAngle = min(track.minKneeAngle, kneeAngle)
                track.kneeExtension = max(track.kneeExtension, kneeAngle - track.minKneeAngle)
            }
            if (signalSpeed >= kickMs) {
                if (track.aboveStartedAt == 0L) track.aboveStartedAt = nowMs
                track.aboveThresholdMs = nowMs - track.aboveStartedAt
                track.aboveThresholdSamples += 1
            } else {
                track.aboveStartedAt = 0L
            }
        }

        if (track.state != SwingState.FOLLOW_THROUGH) return null
        val swingAge = nowMs - track.swingStartedAt
        val pathOk = track.maxDisplacement >= MIN_PATH_M ||
            track.maxLift >= MIN_LIFT_M ||
            track.kneeExtension >= MIN_KNEE_EXTENSION_DEG
        if (swingAge < MIN_SWING_MS) return null
        if (swingAge > SWING_TIMEOUT_MS) {
            track.lastReject = if (track.peakSpeed >= kickMs) "short" else "soft"
            track.state = SwingState.REST
            return null
        }
        if (!pathOk) {
            track.lastReject = "short"
            return null
        }
        if (track.peakSpeed < kickMs) {
            track.lastReject = "soft"
            return null
        }
        if (!canKick) {
            track.lastReject = gateReject ?: "gated"
            return null
        }

        track.lastReject = null
        val f = min(fMax * 1.5f, track.peakForce).roundToInt()
        val speedPower = (track.peakSpeed / max(kickMs * 1.8f, 0.1f)).coerceIn(0f, 1f)
        val power = max(min(1f, f / fMax), speedPower * 0.85f)
        val dirDeg = (
            atan2(-track.peakVy.toDouble(), abs(track.peakVx).coerceAtLeast(0.01f).toDouble()) *
                180.0 / PI
            ).roundToInt()
        val height = when {
            aimHandY != null && aimHandY < 0.38f -> "H"
            track.peakVy < -1.2f -> "H"
            else -> "L"
        }
        val spin = (track.peakVx / max(0.5f, track.peakSpeed)).coerceIn(-1f, 1f)
        val strike = if (track.peakVy < -2.0f && abs(track.peakVx) < track.peakSpeed * 0.55f) {
            "chip"
        } else {
            "drive"
        }
        val landmarkConfidence = (
            track.lastAnkleVis + track.lastHeelVis + track.lastFootVis
            ) / 3f
        val speedScore = (track.peakSpeed / max(kickMs * 1.8f, 0.1f)).coerceIn(0f, 1f)
        val pathScore = (track.maxDisplacement / 0.28f).coerceIn(0f, 1f)
        val temporalConfidence = if (track.lastFlowSamples >= 2) {
            track.lastFlowConfidence
        } else {
            (track.aboveThresholdSamples / 3f).coerceIn(0f, 1f)
        }
        val stateConfidence = (
            0.35f * landmarkConfidence.coerceIn(0f, 1f) +
                0.25f * speedScore + 0.25f * pathScore + 0.15f * temporalConfidence
            ).coerceIn(0f, 1f)
        return Candidate(
            event = ForcePoseEngine.KickEvent(
                zone = zone,
                power = power,
                forceN = f,
                dirDeg = dirDeg,
                foot = track.side,
                peakSpeed = track.peakSpeed,
                height = height,
                spin = spin,
                strike = strike,
                kinematics = KickKinematicState(
                    peakFootSpeedMps = track.peakSpeed,
                    lateralVelocityMps = track.peakVx,
                    upwardVelocityMps = -track.peakVy,
                    pathDisplacementM = track.maxDisplacement,
                    liftM = track.maxLift,
                    swingDurationMs = swingAge,
                    confidence = stateConfidence,
                ),
            ),
            score = track.peakSpeed + track.maxDisplacement,
        )
    }

    private fun weightedFoot(
        landmarks: List<FloatArray>,
        visibility: FloatArray,
        aspect: Float,
        ankle: Int,
        heel: Int,
        foot: Int,
    ): Point? {
        var weight = 0f
        var x = 0f
        var y = 0f
        for (index in intArrayOf(ankle, heel, foot)) {
            val confidence = visibility[index].coerceIn(0f, 1f)
            if (confidence < MIN_LANDMARK_VISIBILITY) continue
            val w = confidence * confidence
            x += landmarks[index][0] * aspect * w
            y += landmarks[index][1] * w
            weight += w
        }
        return if (weight > 0f) Point(x / weight, y / weight) else null
    }

    private fun rawPoint(landmarks: List<FloatArray>, index: Int, aspect: Float) =
        Point(landmarks[index][0] * aspect, landmarks[index][1])

    private fun kneeAngle(hip: Point, knee: Point, ankle: Point, visible: Boolean): Float? {
        if (!visible) return null
        val ax = hip.x - knee.x
        val ay = hip.y - knee.y
        val bx = ankle.x - knee.x
        val by = ankle.y - knee.y
        val denom = sqrt((ax * ax + ay * ay) * (bx * bx + by * by))
        if (denom < 1e-5f) return null
        val cosine = ((ax * bx + ay * by) / denom).coerceIn(-1f, 1f)
        return (acos(cosine) * 180f / PI.toFloat())
    }

    private fun midpoint(a: Point, b: Point) = Point((a.x + b.x) * 0.5f, (a.y + b.y) * 0.5f)
    private fun distance(a: Point, b: Point) = hypot(a.x - b.x, a.y - b.y)

    companion object {
        private const val HISTORY_MS = 900L
        private const val COOLDOWN_MS = 900L
        private const val MIN_SWING_MS = 70L
        private const val PEAK_WAIT_MS = 120L
        private const val WINDUP_TIMEOUT_MS = 650L
        private const val SWING_TIMEOUT_MS = 700L
        private const val REST_SPEED_MPS = 0.55f
        private const val WINDUP_DISPLACEMENT_M = 0.025f
        private const val START_SPEED_RATIO = 0.45f
        private const val FORWARD_SPEED_RATIO = 0.60f
        private const val PEAK_DROP_RATIO = 0.82f
        private const val MIN_PATH_M = 0.09f
        private const val MIN_LIFT_M = 0.035f
        private const val MIN_KNEE_EXTENSION_DEG = 8f
        private const val MIN_LANDMARK_VISIBILITY = 0.18f
        private const val MIN_FLOW_CONFIDENCE = 0.42f
    }
}
