from __future__ import annotations

import asyncio
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import os

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai100 import report_engine


class ReportAnalyticsTests(unittest.TestCase):
    def test_sample_uses_match_telemetry_only(self):
        analytics = report_engine.analyze_match(
            report_engine.sample_shotmap(), 3, "Demo Striker"
        )
        self.assertEqual(analytics["playerName"], "DEMO STRIKER")
        self.assertEqual(analytics["sport"], "football")
        self.assertEqual(analytics["taken"], 3)
        self.assertEqual(analytics["goals"], 2)
        self.assertEqual(analytics["conversionRate"], 66.7)
        self.assertEqual(analytics["averageForce"], 301)
        self.assertEqual(analytics["maxForce"], 346)
        self.assertEqual(analytics["keeperFooled"], 2)
        self.assertEqual(analytics["favoriteZone"], "LEFT CORNER")
        self.assertEqual(analytics["dominantFoot"], "RIGHT")
        self.assertEqual(analytics["drives"], 2)
        self.assertEqual(analytics["chips"], 1)
        self.assertEqual(analytics["nickname"], "THE 2-OF-3 LEFT")
        self.assertEqual(analytics["proBenchmarks"], [])
        self.assertEqual(len(analytics["conversionTable"]), 3)
        self.assertTrue(all(row["kind"] == "attempt" for row in analytics["conversionTable"]))
        self.assertIn("FOOTBALL match", analytics["conversionComparison"])
        self.assertNotIn("Messi", analytics["conversionComparison"])
        self.assertNotIn("Ronaldo", analytics["conversionComparison"])

    def test_report_renders_png_and_one_page_pdf_without_ai100(self):
        analytics = report_engine.analyze_match(report_engine.sample_shotmap(), 3)
        png = report_engine.render_report(analytics, None, {"source": "procedural"})
        image = Image.open(io.BytesIO(png))
        self.assertEqual(image.size, report_engine.REPORT_SIZE)
        self.assertEqual(image.format, "PNG")
        pdf = report_engine.png_to_pdf(png)
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_generation_endpoint_preserves_qualcomm_v2(self):
        self.assertEqual(
            report_engine.generation_endpoint("https://aisuite.cirrascale.com/apis/v2"),
            "https://aisuite.cirrascale.com/apis/v2/images/generations",
        )

    def test_cirrascale_normalizes_sdxl_model_alias(self):
        with patch.dict(os.environ, {
            "AI100_BASE_URL": "https://aisuite.cirrascale.com/apis/v2",
            "AI100_API_KEY": "test-key",
            "AI100_MODEL": "sdxl-turbo",
        }, clear=False):
            settings = report_engine.AI100Settings.from_env()
        self.assertEqual(settings.model, "stabilityai/sdxl-turbo")

    def test_darts_counts_hits_and_points(self):
        shots = [
            {"kick": 1, "zone": "C", "power": 0.8, "force": 210, "result": "hit", "points": 100},
            {"kick": 2, "zone": "L", "power": 0.7, "force": 180, "result": "hit", "points": 60},
            {"kick": 3, "zone": "R", "power": 0.5, "force": 120, "result": "miss", "points": 0},
        ]
        analytics = report_engine.analyze_match(shots, 3, "Dart Player", sport="darts")
        self.assertEqual(analytics["sport"], "darts")
        self.assertEqual(analytics["hits"], 2)
        self.assertEqual(analytics["goals"], 2)
        self.assertEqual(analytics["points"], 160)
        self.assertEqual(analytics["conversionRate"], 66.7)
        self.assertEqual(analytics["scoreLabel"], "HITS")
        self.assertEqual(analytics["attemptWord"], "THROW")
        self.assertIn("DARTS match", analytics["conversionComparison"])
        self.assertEqual(analytics["proBenchmarks"], [])
        self.assertNotIn("ELITE", [row["name"] for row in analytics["conversionTable"]])

    def test_basketball_artwork_prompt_mentions_hoop(self):
        shots = [
            {"kick": 1, "zone": "C", "power": 0.9, "force": 260, "result": "hit", "points": 100, "goalZ": 2.0},
            {"kick": 2, "zone": "L", "power": 0.8, "force": 220, "result": "hit", "points": 40, "goalZ": 2.1},
            {"kick": 3, "zone": "R", "power": 0.4, "force": 140, "result": "miss", "points": 0, "goalZ": 2.3},
        ]
        analytics = report_engine.analyze_match(shots, 3, sport="basketball")
        self.assertEqual(analytics["sport"], "basketball")
        self.assertEqual(analytics["hits"], 2)
        self.assertEqual(analytics["taken"], 3)
        prompt = report_engine.artwork_prompt(analytics)
        self.assertIn("basketball", prompt.lower())
        self.assertIn("hoop", prompt.lower())
        self.assertIn("2 of 3", prompt)


class ReportStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_uses_unguessable_token_and_expires_assets(self):
        class OfflineArtwork:
            async def generate(self, _prompt):
                return None, {"source": "procedural", "elapsedMs": 0}

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            with patch.object(report_engine, "DATA", target):
                store = report_engine.ReportStore()
                store.artwork = OfflineArtwork()
                card = await store.create(report_engine.sample_shotmap(), 3, "Test Player")
                self.assertEqual(card["status"], "ready")
                self.assertGreaterEqual(len(card["token"]), 20)
                self.assertTrue(store.asset(card["token"], "png").is_file())
                self.assertTrue(store.asset(card["token"], "pdf").is_file())
                self.assertIsNotNone(store.metadata(card["token"]))
                qr = report_engine.qr_png(f"http://localhost/report/{card['token']}")
                self.assertTrue(qr.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
