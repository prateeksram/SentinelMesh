package com.sentinelmesh.gesturefootball.forcepose

import com.sentinelmesh.gesturefootball.pose.KickKinematicState
import com.sentinelmesh.gesturefootball.profile.PlayerProfile
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * Wrist / hand throw detector for darts and basketball.
 * Emits the same [ForcePoseEngine.KickEvent] wire shape so the host referee
 * stays unchanged. Feet are ignored — release is hand-only.
 */
class HandThrowEngine(
    bodyKg: Float = 70f,
    throwMs: Float = DEFAULT_THROW_MS,
    torsoM: Float = 0.52f,
    private val fMax: Float = 280f,
) {
    private var armKg = 0.05f * bodyKg
    private var torsoM = torsoM
    private var throwMs = max(FLOOR_MS, throwMs)
    private val sg = floatArrayOf(-2f, 3f, 6f, 7f, 6f, 3f, -2f).map { it / 21f }

    private data class Sample(val t: Double, val x: Float, val y: Float)

    private class Track {
        val raw = ArrayDeque<Sample>()
        val buf = ArrayDeque<Sample>()
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

    private val handL = Track()
    private val handR = Track()
    private var torsoEma = 0f

    var swingPeak = 0f
        private set
    var liveForce = 0f
        private set
    var liveSpeed = 0f
        private set
    var liveHand: String = "R"
        private set
    var lastReject: String? = null
        private set

    fun applyProfile(profile: PlayerProfile) {
        armKg = 0.05f * profile.weightKg
        torsoM = profile.torsoM
        throwMs = max(FLOOR_MS, profile.throwMs ?: DEFAULT_THROW_MS)
    }

    fun setThrowThreshold(ms: Float) {
        throwMs = max(PRACTICE_FLOOR_MS, ms)
    }

    fun resetSwing() {
        swingPeak = 0f
    }

    fun resetBuffers() {
        handL.clear()
        handR.clear()
        lastReject = null
    }

    fun consumeReject(): String? {
        val r = lastReject
        lastReject = null
        return r
    }

    fun update(
        nowMs: Long,
        leftWristX: Float, leftWristY: Float, leftVis: Float,
        rightWristX: Float, rightWristY: Float, rightVis: Float,
        shoulderMidX: Float, shoulderMidY: Float,
        hipMidX: Float, hipMidY: Float,
        zone: String,
        canThrow: Boolean,
        frameAspect: Float = 1f,
    ): ForcePoseEngine.KickEvent? {
        val torsoLen = hypot(shoulderMidX - hipMidX, shoulderMidY - hipMidY)
        if (torsoLen < 0.05f) return null
        torsoEma = if (torsoEma <= 0f) torsoLen else 0.9f * torsoEma + 0.1f * torsoLen
        val mPerUnit = torsoM / torsoEma
        val now = nowMs / 1000.0

        var live = 0f
        var speedLive = 0f
        var handLive = liveHand
        var event: ForcePoseEngine.KickEvent? = null

        for ((side, wx, wy, vis) in listOf(
            Quad("L", leftWristX, leftWristY, leftVis),
            Quad("R", rightWristX, rightWristY, rightVis),
        )) {
            val track = if (side == "L") handL else handR
            if (vis < 0.35f) {
                track.overCount = 0
                continue
            }
            val sm = medianPush(track, now, wx * mPerUnit, wy * mPerUnit)
            if (track.buf.size < 9) continue

            val i = track.buf.size - 3
            val v1 = velAt(track.buf, i)
            val v0 = velAt(track.buf, i - 1)
            val speed = hypot(v1.first, v1.second)
            val dt = (track.buf[i].t - track.buf[i - 1].t).toFloat().coerceAtLeast(1f / 30f)
            val accel = hypot(v1.first - v0.first, v1.second - v0.second) / dt
            val force = armKg * accel
            live = max(live, force)
            if (speed > speedLive) {
                speedLive = speed
                handLive = side
            }

            if (speed < 0.6f) {
                track.restY.addLast(sm.y)
                while (track.restY.size > 25) track.restY.removeFirst()
            }

            if (!canThrow) {
                track.overCount = 0
                track.softCount = 0
                continue
            }
            swingPeak = max(swingPeak, force)

            if (speed > throwMs) {
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
                    // Throw: snap forward / down from a raised hand, or clear travel.
                    val dropped = sm.y - restingY > 0.03f // y grows downward
                    val snap = abs(v1.first) > 0.9f * abs(v1.second) || v1.second > 0.8f
                    if (!(dropped || snap || disp >= 0.12f)) {
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
                        // Hand releases are aimed high (board / hoop); chip-up → H.
                        val height = if (v1.second < -0.6f || track.startY < sm.y) "H" else "L"
                        val spin = (v1.first / max(0.5f, speed)).coerceIn(-1f, 1f)
                        val aspect = frameAspect.coerceIn(0.25f, 4f)
                        val stateVx = v1.first * aspect
                        val stateVy = v1.second
                        val stateSpeed = hypot(stateVx, stateVy)
                        val stateDx = (sm.x - track.startX) * aspect
                        val stateDy = sm.y - track.startY
                        val stateDisplacement = hypot(stateDx, stateDy)
                        val pathScore = (stateDisplacement / 0.22f).coerceIn(0f, 1f)
                        val speedScore = (stateSpeed / max(throwMs * 1.8f, 0.1f)).coerceIn(0f, 1f)
                        val stateConfidence = (
                            0.35f + 0.20f * vis.coerceIn(0f, 1f) +
                                0.25f * pathScore + 0.20f * speedScore
                            ).coerceIn(0f, 1f)
                        event = ForcePoseEngine.KickEvent(
                            zone = zone,
                            power = power,
                            forceN = f,
                            dirDeg = dirDeg,
                            foot = side,
                            peakSpeed = speed,
                            height = height,
                            spin = spin,
                            strike = "drive",
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
                if (speed > throwMs * 0.55f) {
                    track.softCount++
                    if (track.softCount >= SWING_FRAMES) lastReject = "soft"
                } else {
                    track.softCount = 0
                }
                track.overCount = 0
            }
        }

        liveSpeed = speedLive
        liveHand = handLive
        liveForce = if (speedLive < 0.4f) 0f else live
        return event
    }

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

    private data class Quad(val side: String, val x: Float, val y: Float, val vis: Float)

    companion object {
        const val DEFAULT_THROW_MS = 2.2f
        const val FLOOR_MS = 1.2f
        const val PRACTICE_FLOOR_MS = 1.0f
        private const val SWING_FRAMES = 3
    }
}
