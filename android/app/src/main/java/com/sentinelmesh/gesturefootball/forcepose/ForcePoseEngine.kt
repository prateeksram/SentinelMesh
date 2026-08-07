package com.sentinelmesh.gesturefootball.forcepose

import com.sentinelmesh.gesturefootball.profile.PlayerProfile
import com.sentinelmesh.gesturefootball.pose.KickKinematicState
import com.sentinelmesh.gesturefootball.pose.ShotTrajectory
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * ForcePose pipeline (arXiv:2503.22363) — pose-only port.
 * Median filter → Savitzky–Golay → torso-normalized metres → F = m_leg × a_peak.
 *
 * Kick validation is foot/leg only: sustained over-threshold speed plus a real
 * travel path (lift / forward / displacement). Wrists are ignored during the
 * swing — natural aim/balance arm motion must not veto a kick (calib or match).
 * Aim zone (L/C/R) is decided elsewhere from the hand.
 */
class ForcePoseEngine(
    bodyKg: Float = 70f,
    kickMs: Float = 3.0f,
    torsoM: Float = 0.52f,
    private val fMax: Float = 380f,
) {
    private var legKg = 0.0618f * bodyKg
    private var torsoM = torsoM
    private var kickMs = max(FLOOR_MS, kickMs)
    private val sg = floatArrayOf(-2f, 3f, 6f, 7f, 6f, 3f, -2f).map { it / 21f }

    data class Sample(val t: Double, val x: Float, val y: Float)
    data class KickEvent(
        val zone: String,
        val power: Float,
        val forceN: Int,
        val dirDeg: Int,
        val foot: String = "R",
        val peakSpeed: Float = 0f,
        /** High / Low aim band. */
        val height: String = "L",
        /** Lateral spin cue ∈ [-1, 1]. */
        val spin: Float = 0f,
        /** chip | drive */
        val strike: String = "drive",
        /** Source-neutral motion state used by the visualization model. */
        val kinematics: KickKinematicState? = null,
        /** Optional pose-derived trajectory; match scoring still has a legacy fallback. */
        val trajectory: ShotTrajectory? = null,
    )

    /** Per-joint motion track: raw ring for the median tap + smoothed buffer. */
    private class Track {
        val raw = ArrayDeque<Sample>()
        val buf = ArrayDeque<Sample>()

        /** Foot y (metres) while slow — rolling resting band for the lift check. */
        val restY = ArrayDeque<Float>()
        var overCount = 0
        var softCount = 0
        var startX = 0f
        var startY = 0f
        var startT = 0.0

        fun clear() {
            raw.clear()
            buf.clear()
            restY.clear()
            overCount = 0
            softCount = 0
            startT = 0.0
        }
    }

    private val footL = Track()
    private val footR = Track()
    private var torsoEma = 0f

    var swingPeak = 0f
        private set
    var liveForce = 0f
        private set
    var liveSpeed = 0f
        private set
    var liveFoot: String = "R"
        private set

    /** Why the last almost-kick was rejected: "soft" | "short" | null. */
    var lastReject: String? = null
        private set

    fun applyProfile(profile: PlayerProfile) {
        legKg = 0.0618f * profile.weightKg
        torsoM = profile.torsoM
        kickMs = max(FLOOR_MS, profile.kickMs)
    }

    /** Calibration may lower the bar a little to measure practice swings. */
    fun setKickThreshold(ms: Float) {
        kickMs = max(PRACTICE_FLOOR_MS, ms)
    }

    fun resetSwing() {
        swingPeak = 0f
    }

    /** Person switch / tracker jump: motion history belongs to another body. */
    fun resetBuffers() {
        footL.clear()
        footR.clear()
        lastReject = null
    }

    /** One-shot read of the last rejection reason (for corrective coaching). */
    fun consumeReject(): String? {
        val r = lastReject
        lastReject = null
        return r
    }

    @Suppress("UNUSED_PARAMETER")
    fun update(
        nowMs: Long,
        leftFootX: Float, leftFootY: Float, leftVis: Float,
        rightFootX: Float, rightFootY: Float, rightVis: Float,
        shoulderMidX: Float, shoulderMidY: Float,
        hipMidX: Float, hipMidY: Float,
        zone: String,
        canKick: Boolean,
        aimHandY: Float? = null,
        // Kept for call-site compatibility; wrists are not used for kick validation.
        leftWristX: Float = -1f, leftWristY: Float = -1f, leftWristVis: Float = 0f,
        rightWristX: Float = -1f, rightWristY: Float = -1f, rightWristVis: Float = 0f,
        /** Pixel width / height; used only to make exported motion state metric-like. */
        frameAspect: Float = 1f,
    ): KickEvent? {
        val torsoLen = hypot(shoulderMidX - hipMidX, shoulderMidY - hipMidY)
        if (torsoLen < 0.05f) return null
        // EMA keeps the metre scale steady — otherwise landmark jitter at
        // distance turns straight into phantom m/s.
        torsoEma = if (torsoEma <= 0f) torsoLen else 0.9f * torsoEma + 0.1f * torsoLen
        val mPerUnit = torsoM / torsoEma
        val now = nowMs / 1000.0

        var live = 0f
        var speedLive = 0f
        var footLive = liveFoot
        var event: KickEvent? = null

        for ((side, fx, fy, vis) in listOf(
            Quad("L", leftFootX, leftFootY, leftVis),
            Quad("R", rightFootX, rightFootY, rightVis),
        )) {
            val track = if (side == "L") footL else footR
            if (vis < 0.4f) {
                track.overCount = 0
                continue
            }
            val sm = medianPush(track, now, fx * mPerUnit, fy * mPerUnit)
            if (track.buf.size < 9) continue

            val i = track.buf.size - 3
            val v1 = velAt(track.buf, i)
            val v0 = velAt(track.buf, i - 1)
            val speed = hypot(v1.first, v1.second)
            val dt = (track.buf[i].t - track.buf[i - 1].t).toFloat().coerceAtLeast(1f / 30f)
            val accel = hypot(v1.first - v0.first, v1.second - v0.second) / dt
            val force = legKg * accel
            live = max(live, force)
            if (speed > speedLive) {
                speedLive = speed
                footLive = side
            }

            // Remember where this foot rests while it is slow.
            if (speed < 0.8f) {
                track.restY.addLast(sm.y)
                while (track.restY.size > 25) track.restY.removeFirst()
            }

            if (!canKick) {
                track.overCount = 0
                track.softCount = 0
                continue
            }
            swingPeak = max(swingPeak, force)

            if (speed > kickMs) {
                if (track.overCount == 0) {
                    track.startX = sm.x
                    track.startY = sm.y
                    track.startT = sm.t
                }
                track.overCount++
                track.softCount = 0
                if (track.overCount >= SWING_FRAMES && event == null) {
                    val disp = hypot(sm.x - track.startX, sm.y - track.startY)
                    val restingY = median(track.restY) ?: sm.y
                    val lifted = restingY - sm.y > 0.04f // metres; y shrinks upward
                    val forward = abs(v1.first) > 1.2f * abs(v1.second)
                    // Foot-only path gate — wrists never participate.
                    if (!(lifted || forward || disp >= 0.18f)) {
                        lastReject = "short"
                    } else {
                        lastReject = null
                        track.overCount = 0
                        val f = min(fMax * 1.5f, max(swingPeak, force)).roundToInt()
                        val power = min(1f, f / fMax)
                        val dirDeg = (
                            atan2(-v1.second.toDouble(), abs(v1.first).toDouble()) *
                                180.0 / Math.PI
                            ).roundToInt()
                        // Screen Y grows downward — high hand / upward foot path → height H.
                        val height = when {
                            aimHandY != null && aimHandY < 0.38f -> "H"
                            v1.second < -1.2f -> "H"
                            else -> "L"
                        }
                        val spin = (v1.first / max(0.5f, speed)).coerceIn(-1f, 1f)
                        val strike = if (v1.second < -2.0f && abs(v1.first) < speed * 0.55f) {
                            "chip"
                        } else {
                            "drive"
                        }
                        // Preserve the calibrated detector above, but export
                        // aspect-correct state so every pose backend shares
                        // the same physical coordinate convention.
                        val stateVx = v1.first * frameAspect.coerceIn(0.25f, 4f)
                        val stateVy = v1.second
                        val stateSpeed = hypot(stateVx, stateVy)
                        val stateDx = (sm.x - track.startX) * frameAspect.coerceIn(0.25f, 4f)
                        val stateDy = sm.y - track.startY
                        val stateDisplacement = hypot(stateDx, stateDy)
                        val pathScore = (stateDisplacement / 0.28f).coerceIn(0f, 1f)
                        val speedScore = (stateSpeed / max(kickMs * 1.8f, 0.1f)).coerceIn(0f, 1f)
                        val stateConfidence = (
                            0.35f + 0.20f * vis.coerceIn(0f, 1f) +
                                0.25f * pathScore + 0.20f * speedScore
                            ).coerceIn(0f, 1f)
                        event = KickEvent(
                            zone = zone,
                            power = power,
                            forceN = f,
                            dirDeg = dirDeg,
                            foot = side,
                            peakSpeed = speed,
                            height = height,
                            spin = spin,
                            strike = strike,
                            kinematics = KickKinematicState(
                                peakFootSpeedMps = stateSpeed,
                                lateralVelocityMps = stateVx,
                                upwardVelocityMps = -stateVy,
                                pathDisplacementM = stateDisplacement,
                                liftM = (restingY - sm.y).coerceAtLeast(0f),
                                swingDurationMs = ((sm.t - track.startT) * 1000.0)
                                    .toLong().coerceAtLeast(0L),
                                confidence = stateConfidence,
                            ),
                        )
                    }
                }
            } else {
                // Sub-threshold activity is a useful corrective signal ("harder!").
                if (speed > kickMs * 0.55f) {
                    track.softCount++
                    if (track.softCount >= SWING_FRAMES) lastReject = "soft"
                } else {
                    track.softCount = 0
                }
                track.overCount = 0
            }
        }

        liveSpeed = speedLive
        liveFoot = footLive
        // A standing player generates zero force — jitter is not effort.
        liveForce = if (speedLive < 0.5f) 0f else live
        return event
    }

    /** 3-tap median on raw samples kills single-frame landmark jumps. */
    private fun medianPush(t: Track, now: Double, mx: Float, my: Float): Sample {
        t.raw.addLast(Sample(now, mx, my))
        while (t.raw.size > 3) t.raw.removeFirst()
        val xs = t.raw.map { it.x }.sorted()
        val ys = t.raw.map { it.y }.sorted()
        val s = Sample(now, xs[xs.size / 2], ys[ys.size / 2])
        t.buf.addLast(s)
        while (t.buf.size > 12) t.buf.removeFirst()
        return s
    }

    private fun median(values: ArrayDeque<Float>): Float? {
        if (values.isEmpty()) return null
        val sorted = values.sorted()
        return sorted[sorted.size / 2]
    }

    private fun sgAt(buf: List<Sample>, i: Int, key: (Sample) -> Float): Float {
        var s = 0f
        for (j in -3..3) {
            val idx = (i + j).coerceIn(0, buf.lastIndex)
            s += sg[j + 3] * key(buf[idx])
        }
        return s
    }

    private fun velAt(buf: List<Sample>, i: Int): Pair<Float, Float> {
        val dt = (buf[i + 1].t - buf[i - 1].t).toFloat().coerceAtLeast(1f / 15f)
        val vx = (sgAt(buf, i + 1) { it.x } - sgAt(buf, i - 1) { it.x }) / dt
        val vy = (sgAt(buf, i + 1) { it.y } - sgAt(buf, i - 1) { it.y }) / dt
        return vx to vy
    }

    private data class Quad(
        val side: String,
        val x: Float,
        val y: Float,
        val vis: Float,
    )

    companion object {
        /** Hard floor for a saved match kick threshold (m/s). */
        const val FLOOR_MS = 1.7f

        /** Practice swings may go a bit lower while measuring. */
        const val PRACTICE_FLOOR_MS = 1.5f

        /** Consecutive over-threshold frames a swing must sustain (~100 ms). */
        private const val SWING_FRAMES = 3
    }
}
