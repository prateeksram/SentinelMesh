package com.sentinelmesh.gesturefootball.pose

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class EdgeKickEngineTest {
    @Test
    fun timeBasedSwingFiresWithoutThreeConsecutiveThresholdFrames() {
        val engine = EdgeKickEngine(bodyKg = 75f, kickMs = 1.5f, torsoM = 0.50f)
        engine.setKickThreshold(1.5f)
        val visibility = FloatArray(33) { 0.95f }
        val positions = listOf(
            0.50f to 0.90f,
            0.48f to 0.87f,
            0.44f to 0.78f,
            0.34f to 0.66f,
            0.31f to 0.62f,
        )

        var kickFound = false
        positions.forEachIndexed { index, foot ->
            val result = engine.update(
                nowMs = 1_000L + index * 100L,
                landmarks = landmarks(foot.first, foot.second),
                visibility = visibility,
                frameWidth = 640,
                frameHeight = 480,
                zone = "C",
                canKick = true,
                gateReject = null,
                aimHandY = null,
                flow = null,
            )
            kickFound = kickFound || result.kick != null
        }

        assertTrue("a fast displaced swing should register", kickFound)
    }

    @Test
    fun opticalFlowPeakCanSupplyMissingTemporalResolution() {
        val engine = EdgeKickEngine(bodyKg = 75f, kickMs = 1.5f, torsoM = 0.50f)
        engine.setKickThreshold(1.5f)
        val visibility = FloatArray(33) { 0.95f }
        val strong = EdgeKickEngine.FlowFoot(
            vxNorm = -0.75f,
            vyNorm = -0.45f,
            peakVxNorm = -1.0f,
            peakVyNorm = -0.55f,
            dxNorm = -0.06f,
            dyNorm = -0.04f,
            confidence = 0.9f,
            samples = 3,
        )
        val quiet = strong.copy(
            vxNorm = -0.08f,
            vyNorm = -0.04f,
            peakVxNorm = -0.10f,
            peakVyNorm = -0.05f,
        )
        val flowFrames = listOf(null, strong, strong, quiet, quiet)

        var kick = flowFrames.mapIndexedNotNull { index, flow ->
            engine.update(
                nowMs = 2_000L + index * 100L,
                landmarks = landmarks(0.50f, 0.90f),
                visibility = visibility,
                frameWidth = 640,
                frameHeight = 480,
                zone = "L",
                canKick = true,
                gateReject = null,
                aimHandY = 0.3f,
                flow = flow?.let {
                    EdgeKickEngine.FlowMotion(
                        timestampNs = (2_000L + index * 100L) * 1_000_000L,
                        fps = 30f,
                        left = it,
                    )
                },
            ).kick
        }.firstOrNull()

        assertNotNull("high-rate flow should recover a kick peak between poses", kick)
        assertEquals("L", kick?.foot)
        assertNotNull("edge kicks expose the shared kinematic state", kick?.kinematics)
    }

    @Test
    fun validSwingReportsGameplayGateInsteadOfFiring() {
        val engine = EdgeKickEngine(bodyKg = 75f, kickMs = 1.5f, torsoM = 0.50f)
        engine.setKickThreshold(1.5f)
        val visibility = FloatArray(33) { 0.95f }
        val positions = listOf(
            0.50f to 0.90f,
            0.48f to 0.87f,
            0.44f to 0.78f,
            0.34f to 0.66f,
            0.31f to 0.62f,
        )
        var last: EdgeKickEngine.Result? = null

        positions.forEachIndexed { index, foot ->
            last = engine.update(
                nowMs = 3_000L + index * 100L,
                landmarks = landmarks(foot.first, foot.second),
                visibility = visibility,
                frameWidth = 640,
                frameHeight = 480,
                zone = "C",
                canKick = false,
                gateReject = "not in shoot",
                aimHandY = null,
                flow = null,
            )
            assertNull(last?.kick)
        }

        assertEquals("not in shoot", last?.diagnostics?.reject)
    }

    private fun landmarks(leftFootX: Float, leftFootY: Float): List<FloatArray> {
        val points = MutableList(33) { floatArrayOf(0.5f, 0.5f, 0f) }
        points[PoseAnalyzer.L_SHO] = floatArrayOf(0.43f, 0.30f, 0f)
        points[PoseAnalyzer.R_SHO] = floatArrayOf(0.57f, 0.30f, 0f)
        points[PoseAnalyzer.L_HIP] = floatArrayOf(0.46f, 0.55f, 0f)
        points[PoseAnalyzer.R_HIP] = floatArrayOf(0.54f, 0.55f, 0f)
        points[PoseAnalyzer.L_KNEE] = floatArrayOf(0.47f, 0.72f, 0f)
        points[PoseAnalyzer.R_KNEE] = floatArrayOf(0.55f, 0.72f, 0f)
        points[PoseAnalyzer.L_ANK] = floatArrayOf(leftFootX, leftFootY, 0f)
        points[PoseAnalyzer.L_HEEL] = floatArrayOf(leftFootX - 0.01f, leftFootY, 0f)
        points[PoseAnalyzer.L_FOOT] = floatArrayOf(leftFootX + 0.01f, leftFootY, 0f)
        points[PoseAnalyzer.R_ANK] = floatArrayOf(0.56f, 0.90f, 0f)
        points[PoseAnalyzer.R_HEEL] = floatArrayOf(0.55f, 0.90f, 0f)
        points[PoseAnalyzer.R_FOOT] = floatArrayOf(0.57f, 0.90f, 0f)
        return points
    }
}
