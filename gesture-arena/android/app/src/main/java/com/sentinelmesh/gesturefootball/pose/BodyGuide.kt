package com.sentinelmesh.gesturefootball.pose

import kotlin.math.abs
import kotlin.math.min

/**
 * Shared stand-here silhouette region (normalized image coords, origin top-left).
 * Overlay draws this box (X mirrored for front camera); detection uses the same fractions.
 */
object BodyGuide {
    const val LEFT = 0.12f
    const val RIGHT = 0.88f
    const val TOP = 0.04f
    const val BOTTOM = 0.98f

    /** Shoulders + hips confidently present (works with NPU's 25-point torso). */
    fun hasTorso(
        landmarks: List<FloatArray>,
        vis: (Int) -> Float,
    ): Boolean {
        if (landmarks.size <= PoseAnalyzer.R_HIP) return false
        fun ok(i: Int, thr: Float = 0.2f) = vis(i) >= thr
        return ok(PoseAnalyzer.L_SHO) && ok(PoseAnalyzer.R_SHO) &&
            ok(PoseAnalyzer.L_HIP) && ok(PoseAnalyzer.R_HIP)
    }

    /**
     * True when a detected torso is roughly inside the stand-here outline.
     * Kept lenient: NPU often synthesises ankles and portrait crops used to
     * fail strict head-to-toe geometry.
     */
    fun contains(
        landmarks: List<FloatArray>,
        vis: (Int) -> Float,
    ): Boolean {
        if (!hasTorso(landmarks, vis)) return false

        fun x(i: Int) = landmarks[i][0]
        fun y(i: Int) = landmarks[i][1]

        val topY = min(y(PoseAnalyzer.L_SHO), y(PoseAnalyzer.R_SHO))
        val hipY = (y(PoseAnalyzer.L_HIP) + y(PoseAnalyzer.R_HIP)) / 2f
        val midX = (
            x(PoseAnalyzer.L_SHO) + x(PoseAnalyzer.R_SHO) +
                x(PoseAnalyzer.L_HIP) + x(PoseAnalyzer.R_HIP)
            ) / 4f

        // Centre-ish horizontally (mirrored preview still maps to ~0.5 when facing cam).
        val inHoriz = midX in (LEFT + 0.02f)..(RIGHT - 0.02f)
        // Shoulders in upper 2/3, hips below shoulders with a real torso length.
        val shouldersPlausible = topY in 0.02f..0.70f
        val hipsBelow = hipY > topY + 0.08f && hipY < 0.95f
        val notTiny = (hipY - topY) > 0.10f
        // Reject extreme lean / off-to-side poses.
        val upright = abs(
            ((x(PoseAnalyzer.L_SHO) + x(PoseAnalyzer.R_SHO)) / 2f) -
                ((x(PoseAnalyzer.L_HIP) + x(PoseAnalyzer.R_HIP)) / 2f)
        ) < 0.22f

        return inHoriz && shouldersPlausible && hipsBelow && notTiny && upright
    }

    /**
     * Soft T-pose: wrists near shoulder height and clearly out from the torso.
     * Front-camera frames are usually unmirrored, so anatomical L can sit on the
     * right of the image — accept either left/right orientation.
     */
    fun isLooseTpose(landmarks: List<FloatArray>): Boolean {
        if (landmarks.size <= PoseAnalyzer.R_WRI) return false
        fun x(i: Int) = landmarks[i][0]
        fun y(i: Int) = landmarks[i][1]
        val lSho = PoseAnalyzer.L_SHO
        val rSho = PoseAnalyzer.R_SHO
        val lWri = PoseAnalyzer.L_WRI
        val rWri = PoseAnalyzer.R_WRI
        val lElb = 13
        val rElb = 14
        val shoulderY = (y(lSho) + y(rSho)) / 2f
        val shoulderSpan = abs(x(lSho) - x(rSho)).coerceAtLeast(0.04f)
        // Wrists near shoulder height (stricter than before — kills floppy arms).
        val heightOkL = abs(y(lWri) - shoulderY) < 0.16f
        val heightOkR = abs(y(rWri) - shoulderY) < 0.16f
        // Wrists past the elbows horizontally (arms truly extended).
        val outL = abs(x(lWri) - x(lSho)) > abs(x(lElb) - x(lSho)) + shoulderSpan * 0.15f
        val outR = abs(x(rWri) - x(rSho)) > abs(x(rElb) - x(rSho)) + shoulderSpan * 0.15f
        val armSpan = abs(x(lWri) - x(rWri))
        val armsWide = armSpan > shoulderSpan * 1.55f
        val upright = abs(
            ((x(lSho) + x(rSho)) / 2f) -
                ((landmarks[PoseAnalyzer.L_HIP][0] + landmarks[PoseAnalyzer.R_HIP][0]) / 2f)
        ) < 0.18f
        return heightOkL && heightOkR && outL && outR && armsWide && upright
    }

    /** Diagnoses which T-pose arm is wrong — for corrective coaching. */
    fun tposeProblem(landmarks: List<FloatArray>): String? {
        if (landmarks.size <= PoseAnalyzer.R_WRI) return "Step into the outline so I can see you."
        fun x(i: Int) = landmarks[i][0]
        fun y(i: Int) = landmarks[i][1]
        val shoulderY = (y(PoseAnalyzer.L_SHO) + y(PoseAnalyzer.R_SHO)) / 2f
        val shoulderSpan = abs(x(PoseAnalyzer.L_SHO) - x(PoseAnalyzer.R_SHO)).coerceAtLeast(0.04f)
        val lHigh = abs(y(PoseAnalyzer.L_WRI) - shoulderY) < 0.16f
        val rHigh = abs(y(PoseAnalyzer.R_WRI) - shoulderY) < 0.16f
        val lOut = abs(x(PoseAnalyzer.L_WRI) - x(PoseAnalyzer.L_SHO)) > shoulderSpan * 0.45f
        val rOut = abs(x(PoseAnalyzer.R_WRI) - x(PoseAnalyzer.R_SHO)) > shoulderSpan * 0.45f
        return when {
            !lHigh && !rHigh -> "Raise both arms level with your shoulders."
            !lHigh -> "Raise your LEFT arm level with your shoulders."
            !rHigh -> "Raise your RIGHT arm level with your shoulders."
            !lOut && !rOut -> "Straighten your arms out like a T."
            !lOut -> "Stretch your LEFT arm further out."
            !rOut -> "Stretch your RIGHT arm further out."
            else -> null
        }
    }
}
