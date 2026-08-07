"""Seeded unit tests for goal thirds and sport referees (no live server)."""

from __future__ import annotations

import random
import unittest
from unittest.mock import MagicMock

import server


class ZoneOfXTests(unittest.TestCase):
    def test_equal_thirds_split_at_zone_third(self):
        # Goal spans ±GOAL_HALF_W_M; thirds split at ±ZONE_THIRD_M (±1.22).
        self.assertEqual(server.zone_of_x(-1.23), "L")
        self.assertEqual(server.zone_of_x(-server.ZONE_THIRD_M - 0.01), "L")
        self.assertEqual(server.zone_of_x(-server.ZONE_THIRD_M), "C")
        self.assertEqual(server.zone_of_x(0.0), "C")
        self.assertEqual(server.zone_of_x(1.0), "C")
        self.assertEqual(server.zone_of_x(server.ZONE_THIRD_M), "C")
        self.assertEqual(server.zone_of_x(server.ZONE_THIRD_M + 0.01), "R")
        self.assertEqual(server.zone_of_x(1.23), "R")

    def test_centers_match_zone_labels(self):
        for zone in "LCR":
            self.assertEqual(server.zone_of_x(server.zone_center_x(zone)), zone)


class RefereeTests(unittest.TestCase):
    def setUp(self):
        self.game = server.Game(MagicMock())

    def test_referee_wrong_zone_almost_always_goal_with_seed(self):
        random.seed(1)
        results = [self.game.referee("L", 0.5, "R") for _ in range(40)]
        self.assertGreaterEqual(results.count("goal"), 30)

    def test_referee_same_zone_low_power_usually_save_or_post(self):
        random.seed(2)
        results = [self.game.referee("C", 0.2, "C") for _ in range(40)]
        self.assertLessEqual(results.count("goal"), 12)

    def test_referee_metric_wide_and_post(self):
        random.seed(3)
        result, zone = self.game.referee_metric(5.0, 1.2, 0.8, "C")
        self.assertEqual(result, "wide")
        self.assertEqual(zone, "R")

        result, zone = self.game.referee_metric(server.GOAL_HALF_W_M + 0.05, 1.5, 0.8, "R")
        self.assertEqual(result, "post")
        self.assertEqual(zone, "R")

    def test_referee_metric_wrong_zone_on_target(self):
        random.seed(4)
        # x=1.0 is center third after the zone_of_x fix; keeper diving L → goal path.
        results = [
            self.game.referee_metric(1.0, 1.2, 0.5, "L")[0] for _ in range(30)
        ]
        self.assertGreaterEqual(results.count("goal"), 25)
        self.assertEqual(server.zone_of_x(1.0), "C")

    def test_referee_target_darts_rings_and_scale(self):
        self.game.sport = "darts"
        self.game.ring_scale = 1.0
        t = server.SPORT_TARGETS["darts"]
        hit, pts = self.game.referee_target(t["cx"], t["cz"])
        self.assertEqual(hit, "hit")
        self.assertEqual(pts, 100)

        miss, pts = self.game.referee_target(t["cx"] + 2.0, t["cz"])
        self.assertEqual(miss, "miss")
        self.assertEqual(pts, 0)

        self.game.ring_scale = 0.5
        # Outer ring at full scale becomes a miss when rings shrink.
        outer_r = t["rings"][-1][0]
        result, pts = self.game.referee_target(t["cx"] + outer_r * 0.8, t["cz"])
        self.assertEqual(result, "miss")
        self.assertEqual(pts, 0)

    def test_referee_target_basketball_bull(self):
        self.game.sport = "basketball"
        self.game.ring_scale = 1.0
        t = server.SPORT_TARGETS["basketball"]
        hit, pts = self.game.referee_target(t["cx"], t["cz"])
        self.assertEqual((hit, pts), ("hit", 100))


if __name__ == "__main__":
    unittest.main()
