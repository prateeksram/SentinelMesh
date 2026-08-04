package com.sentinelmesh.gesturefootball.forcepose

import kotlin.math.atan2
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * ForcePose pipeline (arXiv:2503.22363) — pose-only port.
 * Savitzky–Golay → torso-normalized metres → F = m_leg × a_peak.
 */
class ForcePoseEngine(
    bodyKg: Float = 70f,
    private val kickMs: Float = 3.0f,
    private val fMax: Float = 380f,
) {
    private val legKg = 0.0618f * bodyKg
    private val torsoM = 0.52f
    private val sg = floatArrayOf(-2f, 3f, 6f, 7f, 6f, 3f, -2f).map { it / 21f }

    data class Sample(val t: Double, val x: Float, val y: Float)
    data class KickEvent(
        val zone: String,
        val power: Float,
        val forceN: Int,
        val dirDeg: Int,
    )

    private val footL = ArrayDeque<Sample>()
    private val footR = ArrayDeque<Sample>()
    var swingPeak = 0f
        private set
    var liveForce = 0f
        private set

    fun resetSwing() {
        swingPeak = 0f
    }

    fun update(
        nowMs: Long,
        leftFootX: Float, leftFootY: Float, leftVis: Float,
        rightFootX: Float, rightFootY: Float, rightVis: Float,
        shoulderMidX: Float, shoulderMidY: Float,
        hipMidX: Float, hipMidY: Float,
        zone: String,
        canKick: Boolean,
    ): KickEvent? {
        val torsoLen = hypot(shoulderMidX - hipMidX, shoulderMidY - hipMidY)
        if (torsoLen < 0.05f) return null
        val mPerUnit = torsoM / torsoLen
        val now = nowMs / 1000.0
        var live = 0f
        var event: KickEvent? = null

        for ((_, fx, fy, vis, buf) in listOf(
            Quad("L", leftFootX, leftFootY, leftVis, footL),
            Quad("R", rightFootX, rightFootY, rightVis, footR),
        )) {
            if (vis < 0.4f) continue
            buf.addLast(Sample(now, fx * mPerUnit, fy * mPerUnit))
            while (buf.size > 12) buf.removeFirst()
            if (buf.size < 9) continue

            val i = buf.size - 3
            val v1 = velAt(buf, i)
            val v0 = velAt(buf, i - 1)
            val speed = hypot(v1.first, v1.second)
            val dt = (buf[i].t - buf[i - 1].t).toFloat().coerceAtLeast(1f / 30f)
            val accel = hypot(v1.first - v0.first, v1.second - v0.second) / dt
            val force = legKg * accel
            live = max(live, force)
            if (canKick) swingPeak = max(swingPeak, force)

            if (canKick && speed > kickMs) {
                val f = min(fMax * 1.5f, max(swingPeak, force)).roundToInt()
                val power = min(1f, f / fMax)
                val dirDeg = (atan2(-v1.second.toDouble(), kotlin.math.abs(v1.first).toDouble())
                    * 180.0 / Math.PI).roundToInt()
                event = KickEvent(zone, power, f, dirDeg)
            }
        }
        liveForce = live
        return event
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
        val buf: ArrayDeque<Sample>,
    )
}
