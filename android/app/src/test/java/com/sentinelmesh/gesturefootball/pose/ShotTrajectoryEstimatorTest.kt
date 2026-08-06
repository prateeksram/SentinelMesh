package com.sentinelmesh.gesturefootball.pose

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ShotTrajectoryEstimatorTest {
    private fun state(
        source: String = "GPU",
        speed: Float = 4.2f,
        lateral: Float = 0.4f,
        upward: Float = 1.1f,
    ) = KickKinematicState(
        source = source,
        peakFootSpeedMps = speed,
        lateralVelocityMps = lateral,
        upwardVelocityMps = upward,
        pathDisplacementM = 0.32f,
        liftM = 0.11f,
        swingDurationMs = 360L,
        confidence = 0.82f,
    )

    @Test
    fun sampledPathReachesElevenMetreGoalPlane() {
        val shot = ShotTrajectoryEstimator.estimate(
            zone = "R",
            power = 0.72f,
            height = "L",
            spin = 0.2f,
            strike = "drive",
            state = state(),
        )

        assertNotNull(shot)
        shot!!
        assertEquals(11f, shot.points.last().yM, 0.001f)
        assertEquals(shot.goalXM, shot.points.last().xM, 0.001f)
        assertEquals(shot.goalZM, shot.points.last().zM, 0.001f)
        assertTrue(shot.points.zipWithNext().all { (a, b) -> b.timeS > a.timeS })
        assertTrue(shot.confidence in 0f..1f)
    }

    @Test
    fun inferenceBackendDoesNotChangePhysics() {
        val outputs = listOf("NPU", "GPU", "CPU", "UNO Q").map { source ->
            ShotTrajectoryEstimator.estimate(
                zone = "L",
                power = 0.68f,
                height = "H",
                spin = -0.15f,
                strike = "drive",
                state = state(source = source),
            )!!
        }

        outputs.drop(1).forEach { shot ->
            assertEquals(outputs.first().goalXM, shot.goalXM, 0.0001f)
            assertEquals(outputs.first().goalZM, shot.goalZM, 0.0001f)
            assertEquals(outputs.first().flightTimeS, shot.flightTimeS, 0.0001f)
        }
    }

    @Test
    fun higherKickStateProducesFasterFlight() {
        fun estimate(speed: Float) = ShotTrajectoryEstimator.estimate(
            zone = "C",
            power = 0.7f,
            height = "L",
            spin = 0f,
            strike = "drive",
            state = state(speed = speed),
        )!!

        val slow = estimate(2.2f)
        val fast = estimate(6.0f)
        assertTrue(fast.launchSpeedMps > slow.launchSpeedMps)
        assertTrue(fast.flightTimeS < slow.flightTimeS)
    }
}
