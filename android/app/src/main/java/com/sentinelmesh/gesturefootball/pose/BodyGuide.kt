package com.sentinelmesh.gesturefootball.pose

import kotlin.math.max
import kotlin.math.min

/**
 * Shared stand-here silhouette region (normalized image coords, origin top-left).
 * Overlay draws this box (X mirrored for front camera); detection uses the same fractions.
 */
object BodyGuide {
    const val LEFT = 0.14f
    const val RIGHT = 0.86f
    const val TOP = 0.06f
    const val BOTTOM = 0.96f

    /**
     * True when a detected pose roughly fills the guide: shoulders near top half,
     * feet/hips near bottom, body centred — even if ankle visibility scores are weak.
     */
    fun contains(
        landmarks: List<FloatArray>,
        vis: (Int) -> Float,
    ): Boolean {
        if (landmarks.size <= PoseAnalyzer.R_FOOT) return false

        fun x(i: Int) = landmarks[i][0]
        fun y(i: Int) = landmarks[i][1]
        fun ok(i: Int, thr: Float = 0.25f) = vis(i) >= thr

        val shoulderOk = ok(PoseAnalyzer.L_SHO, 0.3f) && ok(PoseAnalyzer.R_SHO, 0.3f)
        val hipOk = ok(PoseAnalyzer.L_HIP, 0.25f) && ok(PoseAnalyzer.R_HIP, 0.25f)
        if (!shoulderOk || !hipOk) return false

        val leftFoot = ok(PoseAnalyzer.L_ANK, 0.2f) || ok(PoseAnalyzer.L_FOOT, 0.2f)
        val rightFoot = ok(PoseAnalyzer.R_ANK, 0.2f) || ok(PoseAnalyzer.R_FOOT, 0.2f)
        val feetOk = leftFoot && rightFoot

        val topY = min(y(PoseAnalyzer.L_SHO), y(PoseAnalyzer.R_SHO))
        val hipY = (y(PoseAnalyzer.L_HIP) + y(PoseAnalyzer.R_HIP)) / 2f
        val botY = max(
            max(y(PoseAnalyzer.L_ANK), y(PoseAnalyzer.R_ANK)),
            max(y(PoseAnalyzer.L_FOOT), y(PoseAnalyzer.R_FOOT)),
        )
        val midX = (
            x(PoseAnalyzer.L_SHO) + x(PoseAnalyzer.R_SHO) +
                x(PoseAnalyzer.L_HIP) + x(PoseAnalyzer.R_HIP)
            ) / 4f

        val inHoriz = midX in (LEFT + 0.04f)..(RIGHT - 0.04f)
        // Full body should stretch most of the guide vertically.
        val headInGuide = topY in TOP..(TOP + 0.38f)
        val feetInGuide = if (feetOk) {
            botY in (BOTTOM - 0.35f)..BOTTOM
        } else {
            // Soft fallback: hips low enough that legs are likely in frame.
            hipY > 0.48f && (hipY - topY) > 0.18f
        }
        val tallEnough = (botY - topY) > 0.40f || (!feetOk && (hipY - topY) > 0.22f)

        return inHoriz && headInGuide && (feetInGuide || tallEnough && hipY > 0.50f) &&
            (feetOk || tallEnough)
    }
}
