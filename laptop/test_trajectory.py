import math
import unittest

try:
    from .server import Desk, Game, normalize_kick_state, normalize_trajectory
except ImportError:
    from server import Desk, Game, normalize_kick_state, normalize_trajectory


class TrajectoryWireTests(unittest.TestCase):
    def test_generalized_kick_state_is_bounded(self):
        state = normalize_kick_state({
            "schema": "sentinel.kick.state.v1",
            "source": "UNO Q",
            "peakFootSpeedMps": 4.2,
            "lateralVelocityMps": -1.3,
            "upwardVelocityMps": 0.8,
            "pathDisplacementM": 0.31,
            "liftM": 0.12,
            "swingDurationMs": 360,
            "confidence": 1.4,
        })

        self.assertEqual("UNO Q", state["source"])
        self.assertEqual(1.0, state["confidence"])
        self.assertEqual(-1.3, state["lateralVelocityMps"])

    def test_sampled_trajectory_survives_validation(self):
        trajectory = normalize_trajectory({
            "schema": "sentinel.trajectory.v1",
            "model": "sentinel.pose-ballistic.v1",
            "confidence": 0.72,
            "launchVelocity": [1.0, 16.0, 4.0],
            "launchSpeedMps": 16.52,
            "flightTimeS": 0.76,
            "goalX": 1.9,
            "goalZ": 1.2,
            "apexM": 1.4,
            "points": [
                [0.0, 0.0, 0.0, 0.11],
                [0.4, 0.6, 6.0, 1.1],
                [0.76, 1.9, 11.0, 1.2],
            ],
        })

        self.assertIsNotNone(trajectory)
        self.assertEqual(3, len(trajectory["points"]))
        self.assertEqual(11.0, trajectory["points"][-1][2])

    def test_non_finite_or_unsampled_trajectory_is_rejected(self):
        bad = {
            "schema": "sentinel.trajectory.v1",
            "confidence": 0.7,
            "launchVelocity": [0.0, 15.0, 3.0],
            "launchSpeedMps": math.inf,
            "flightTimeS": 0.7,
            "goalX": 0.0,
            "goalZ": 1.0,
            "apexM": 1.2,
            "points": [[0.0, 0.0, 0.0, 0.11]],
        }
        self.assertIsNone(normalize_trajectory(bad))


class TrajectoryGameIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_phone_kick_keeps_optional_state_for_tv_snapshot(self):
        game = Game(Desk())
        phone = object()
        game.sockets[phone] = "phone"
        game.phase = "shoot"
        await game.on_message(phone, {
            "type": "kick",
            "zone": "R",
            "power": 0.75,
            "force": 220,
            "dirDeg": 10,
            "kickState": {
                "schema": "sentinel.kick.state.v1",
                "source": "GPU",
                "peakFootSpeedMps": 4.5,
                "confidence": 0.8,
            },
            "trajectory": {
                "schema": "sentinel.trajectory.v1",
                "model": "sentinel.pose-ballistic.v1",
                "confidence": 0.7,
                "launchVelocity": [1.0, 16.0, 3.0],
                "launchSpeedMps": 16.4,
                "flightTimeS": 0.74,
                "goalX": 2.0,
                "goalZ": 0.8,
                "apexM": 0.9,
                "points": [[0.0, 0.0, 0.0, 0.11], [0.74, 2.0, 11.0, 0.8]],
            },
        })

        self.assertTrue(game.kick_evt.is_set())
        self.assertEqual("GPU", game.kick_msg["kickState"]["source"])
        self.assertEqual(11.0, game.kick_msg["trajectory"]["points"][-1][2])


if __name__ == "__main__":
    unittest.main()
