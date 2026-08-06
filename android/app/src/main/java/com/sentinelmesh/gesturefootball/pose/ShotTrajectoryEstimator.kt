package com.sentinelmesh.gesturefootball.pose

import kotlin.math.max
import kotlin.math.sqrt

/**
 * Source-neutral state captured at the strongest point of a validated swing.
 *
 * Camera coordinates are converted before they enter this contract: lateral
 * velocity is positive toward image right and upward velocity is positive up.
 * The source label is metadata only; trajectory math never branches by pose
 * backend, so NPU/GPU/CPU and remote edge inference share the same model.
 */
data class KickKinematicState(
    val source: String = "UNKNOWN",
    val peakFootSpeedMps: Float,
    val lateralVelocityMps: Float,
    val upwardVelocityMps: Float,
    val pathDisplacementM: Float,
    val liftM: Float,
    val swingDurationMs: Long,
    val confidence: Float,
)

/** x=lateral, y=toward goal, z=height; all distances are metres. */
data class TrajectoryPoint(
    val timeS: Float,
    val xM: Float,
    val yM: Float,
    val zM: Float,
)

data class ShotTrajectory(
    val model: String,
    val confidence: Float,
    val launchVxMps: Float,
    val launchVyMps: Float,
    val launchVzMps: Float,
    val launchSpeedMps: Float,
    val flightTimeS: Float,
    val goalXM: Float,
    val goalZM: Float,
    val apexM: Float,
    val points: List<TrajectoryPoint>,
)

/**
 * Lightweight pose-to-ball model used for visualization only.
 *
 * Monocular pose cannot observe ball contact or velocity toward the goal. We
 * therefore infer forward speed from calibrated-size foot speed, combine the
 * player's aim zone with signed swing state, then integrate gravity, drag and
 * a small lateral curve. A confidence value travels with the estimate so the
 * host can retain its legacy visual whenever state quality is insufficient.
 */
object ShotTrajectoryEstimator {
    private const val MODEL = "sentinel.pose-ballistic.v1"
    private const val GOAL_DISTANCE_M = 11f
    private const val BALL_RADIUS_M = 0.11f
    private const val GRAVITY_MPS2 = 9.81f
    private const val DRAG = 0.012f
    private const val STEP_S = 0.02f
    private const val SAMPLE_EVERY_S = 0.06f
    private const val MAX_FLIGHT_S = 2.0f

    fun estimate(
        zone: String,
        power: Float,
        height: String,
        spin: Float,
        strike: String,
        state: KickKinematicState,
    ): ShotTrajectory? {
        val footSpeed = state.peakFootSpeedMps
        if (!footSpeed.isFinite() || footSpeed < 0.5f || state.confidence <= 0f) return null

        val speedState = ((footSpeed - 1.5f) / 4.5f).coerceIn(0f, 1f)
        val effort = (0.72f * speedState + 0.28f * power.coerceIn(0f, 1f)).coerceIn(0f, 1f)
        val desiredBallSpeed = 11f + 14f * effort
        val lateralCue = (state.lateralVelocityMps / max(footSpeed, 0.5f)).coerceIn(-1f, 1f)
        val upwardCue = (state.upwardVelocityMps / max(footSpeed, 0.5f)).coerceIn(0f, 1f)

        // Aim intent establishes a stable goal region. Kinematic cues add the
        // within-region placement and shape, rather than pretending a single
        // front camera directly observes the ball's depth velocity.
        val zoneCenter = when (zone) {
            "L" -> -2.20f
            "R" -> 2.20f
            else -> 0f
        }
        val targetX = (zoneCenter + lateralCue * 0.42f).coerceIn(-3.35f, 3.35f)
        val targetZ = when {
            strike == "chip" -> 1.65f + upwardCue * 0.45f
            height == "H" -> 1.35f + upwardCue * 0.55f
            else -> 0.48f + upwardCue * 0.42f
        }.coerceIn(0.25f, 2.25f)

        val curveAcceleration = spin.coerceIn(-1f, 1f) * 1.35f
        val flightGuess = (GOAL_DISTANCE_M / (desiredBallSpeed * 0.92f)).coerceIn(0.42f, 1.15f)
        val launchVx = (targetX - 0.5f * curveAcceleration * flightGuess * flightGuess) / flightGuess
        val launchVz = (targetZ - BALL_RADIUS_M + 0.5f * GRAVITY_MPS2 * flightGuess * flightGuess) /
            flightGuess
        val forwardSquared = desiredBallSpeed * desiredBallSpeed - launchVx * launchVx - launchVz * launchVz
        val launchVy = sqrt(max(forwardSquared, desiredBallSpeed * desiredBallSpeed * 0.48f))
        val launchSpeed = sqrt(launchVx * launchVx + launchVy * launchVy + launchVz * launchVz)

        var t = 0f
        var x = 0f
        var y = 0f
        var z = BALL_RADIUS_M
        var vx = launchVx
        var vy = launchVy
        var vz = launchVz
        var apex = z
        var nextSample = 0f
        val points = ArrayList<TrajectoryPoint>(40)
        points += TrajectoryPoint(0f, x, y, z)
        nextSample += SAMPLE_EVERY_S

        var previous = points.last()
        var impact: TrajectoryPoint? = null
        while (t < MAX_FLIGHT_S && y < GOAL_DISTANCE_M) {
            previous = TrajectoryPoint(t, x, y, z)
            val speed = sqrt(vx * vx + vy * vy + vz * vz)
            vx += (curveAcceleration - DRAG * speed * vx) * STEP_S
            vy += (-DRAG * speed * vy) * STEP_S
            vz += (-GRAVITY_MPS2 - DRAG * speed * vz) * STEP_S
            x += vx * STEP_S
            y += vy * STEP_S
            z += vz * STEP_S
            t += STEP_S

            if (z < BALL_RADIUS_M) {
                z = BALL_RADIUS_M
                if (vz < 0f) vz = -vz * 0.34f
                vx *= 0.92f
                vy *= 0.92f
            }
            apex = max(apex, z)

            if (y >= GOAL_DISTANCE_M) {
                val span = (y - previous.yM).coerceAtLeast(1e-4f)
                val u = ((GOAL_DISTANCE_M - previous.yM) / span).coerceIn(0f, 1f)
                impact = TrajectoryPoint(
                    timeS = previous.timeS + (t - previous.timeS) * u,
                    xM = previous.xM + (x - previous.xM) * u,
                    yM = GOAL_DISTANCE_M,
                    zM = previous.zM + (z - previous.zM) * u,
                )
                break
            }
            if (t + 1e-4f >= nextSample) {
                points += TrajectoryPoint(t, x, y, z)
                nextSample += SAMPLE_EVERY_S
            }
        }

        val goalPoint = impact ?: return null
        if (points.last().timeS < goalPoint.timeS) points += goalPoint else points[points.lastIndex] = goalPoint
        val stateConfidence = state.confidence.coerceIn(0f, 1f)
        val pathEvidence = (state.pathDisplacementM / 0.28f).coerceIn(0f, 1f)
        val confidence = (0.55f * stateConfidence + 0.25f * speedState + 0.20f * pathEvidence)
            .coerceIn(0f, 1f)

        return ShotTrajectory(
            model = MODEL,
            confidence = confidence,
            launchVxMps = launchVx,
            launchVyMps = launchVy,
            launchVzMps = launchVz,
            launchSpeedMps = launchSpeed,
            flightTimeS = goalPoint.timeS,
            goalXM = goalPoint.xM,
            goalZM = goalPoint.zM,
            apexM = apex,
            points = points,
        )
    }
}
