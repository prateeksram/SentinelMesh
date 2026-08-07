package com.sentinelmesh.gesturefootball.pose

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ThumbsUpTest {

    @Test
    fun distantRightThumbsUpDetected() {
        // Person ~2.5 m away: hand is a small fraction of the frame.
        val lm = blankPose()
        lm[PoseAnalyzer.L_SHO] = floatArrayOf(0.40f, 0.32f)
        lm[PoseAnalyzer.R_SHO] = floatArrayOf(0.60f, 0.32f)
        lm[PoseAnalyzer.L_HIP] = floatArrayOf(0.43f, 0.55f)
        lm[PoseAnalyzer.R_HIP] = floatArrayOf(0.57f, 0.55f)
        lm[PoseAnalyzer.R_ELB] = floatArrayOf(0.66f, 0.42f)
        lm[PoseAnalyzer.R_WRI] = floatArrayOf(0.70f, 0.48f)
        lm[PoseAnalyzer.R_INDEX] = floatArrayOf(0.705f, 0.465f)
        lm[PoseAnalyzer.R_PINKY] = floatArrayOf(0.695f, 0.468f)
        lm[PoseAnalyzer.R_THUMB] = floatArrayOf(0.702f, 0.445f)

        assertTrue(PoseAnalyzer.isThumbsUp(lm.toList()))
        assertTrue(PoseAnalyzer.isReadyHand(lm.toList()))
    }

    @Test
    fun raisedHandCountsAsReadyWhenDigitsCollapse() {
        val lm = blankPose()
        lm[PoseAnalyzer.L_SHO] = floatArrayOf(0.40f, 0.40f)
        lm[PoseAnalyzer.R_SHO] = floatArrayOf(0.60f, 0.40f)
        // Right arm up; thumb/index collapsed into wrist (typical at distance).
        lm[PoseAnalyzer.R_ELB] = floatArrayOf(0.62f, 0.30f)
        lm[PoseAnalyzer.R_WRI] = floatArrayOf(0.63f, 0.18f)
        lm[PoseAnalyzer.R_THUMB] = floatArrayOf(0.63f, 0.18f)
        lm[PoseAnalyzer.R_INDEX] = floatArrayOf(0.63f, 0.18f)
        lm[PoseAnalyzer.R_PINKY] = floatArrayOf(0.63f, 0.18f)

        assertFalse(PoseAnalyzer.isThumbsUp(lm.toList()))
        assertTrue(PoseAnalyzer.isHandRaised(lm.toList()))
        assertTrue(PoseAnalyzer.isReadyHand(lm.toList()))
    }

    @Test
    fun tPoseDoesNotCountAsReady() {
        val lm = blankPose()
        lm[PoseAnalyzer.L_SHO] = floatArrayOf(0.40f, 0.40f)
        lm[PoseAnalyzer.R_SHO] = floatArrayOf(0.60f, 0.40f)
        lm[PoseAnalyzer.L_ELB] = floatArrayOf(0.28f, 0.40f)
        lm[PoseAnalyzer.L_WRI] = floatArrayOf(0.16f, 0.40f)
        lm[PoseAnalyzer.R_ELB] = floatArrayOf(0.72f, 0.40f)
        lm[PoseAnalyzer.R_WRI] = floatArrayOf(0.84f, 0.40f)

        assertFalse(PoseAnalyzer.isHandRaised(lm.toList()))
        assertFalse(PoseAnalyzer.isReadyHand(lm.toList()))
    }

    @Test
    fun flatPointingHandRejected() {
        val lm = blankPose()
        lm[PoseAnalyzer.L_SHO] = floatArrayOf(0.40f, 0.32f)
        lm[PoseAnalyzer.R_SHO] = floatArrayOf(0.60f, 0.32f)
        lm[PoseAnalyzer.R_ELB] = floatArrayOf(0.66f, 0.42f)
        lm[PoseAnalyzer.R_WRI] = floatArrayOf(0.70f, 0.48f)
        lm[PoseAnalyzer.R_INDEX] = floatArrayOf(0.74f, 0.48f)
        lm[PoseAnalyzer.R_PINKY] = floatArrayOf(0.73f, 0.49f)
        lm[PoseAnalyzer.R_THUMB] = floatArrayOf(0.78f, 0.47f)

        assertFalse(PoseAnalyzer.isThumbsUp(lm.toList()))
    }

    @Test
    fun nullOrShortPoseRejected() {
        assertFalse(PoseAnalyzer.isThumbsUp(null))
        assertFalse(PoseAnalyzer.isReadyHand(null))
        assertFalse(PoseAnalyzer.isThumbsUp(List(10) { floatArrayOf(0f, 0f) }))
    }

    private fun blankPose(): Array<FloatArray> =
        Array(33) { floatArrayOf(0.5f, 0.5f) }
}
