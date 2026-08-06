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
    def test_sample_uses_every_phone_metric_and_ranks_between_legends(self):
        analytics = report_engine.analyze_match(
            report_engine.sample_shotmap(), 5, "Demo Striker"
        )
        self.assertEqual(analytics["playerName"], "DEMO STRIKER")
        self.assertEqual(analytics["goals"], 4)
        self.assertEqual(analytics["conversionRate"], 80.0)
        self.assertEqual(analytics["averageForce"], 321)
        self.assertEqual(analytics["maxForce"], 372)
        self.assertEqual(analytics["averagePower"], 84)
        self.assertEqual(analytics["keeperFooled"], 3)
        self.assertEqual(analytics["favoriteZone"], "LEFT CORNER")
        self.assertGreaterEqual(analytics["unpredictability"], 90)
        self.assertEqual(analytics["dominantFoot"], "RIGHT")
        self.assertEqual(analytics["drives"], 4)
        self.assertEqual(analytics["chips"], 1)
        self.assertEqual(analytics["highShots"], 3)
        self.assertEqual(analytics["lowShots"], 2)
        self.assertEqual(analytics["conversionRank"], 2)
        self.assertIn("Messi", analytics["conversionComparison"])
        self.assertIn("Ronaldo", analytics["conversionComparison"])

    def test_report_renders_png_and_one_page_pdf_without_ai100(self):
        analytics = report_engine.analyze_match(report_engine.sample_shotmap(), 5)
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
                card = await store.create(report_engine.sample_shotmap(), 5, "Test Player")
                self.assertEqual(card["status"], "ready")
                self.assertGreaterEqual(len(card["token"]), 20)
                self.assertTrue(store.asset(card["token"], "png").is_file())
                self.assertTrue(store.asset(card["token"], "pdf").is_file())
                self.assertIsNotNone(store.metadata(card["token"]))
                qr = report_engine.qr_png(f"http://localhost/report/{card['token']}")
                self.assertTrue(qr.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
