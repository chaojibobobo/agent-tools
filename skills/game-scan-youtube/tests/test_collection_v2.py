import importlib.util
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"


def load_collector():
    path = SCRIPTS_DIR / "collect_rss.py"
    spec = importlib.util.spec_from_file_location("collect_rss_v2", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CollectionWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.collector = load_collector()

    def test_window_starts_at_last_successful_cursor(self):
        now = datetime(2026, 8, 11, 2, tzinfo=timezone.utc)
        state = {"last_successful_scan_end": "2026-08-10T00:00:00Z"}

        start, end, limited = self.collector.resolve_window(now, state, 72)

        self.assertEqual("2026-08-10T00:00:00Z", self.collector.format_timestamp(start))
        self.assertEqual(now, end)
        self.assertFalse(limited)

    def test_old_cursor_is_capped_and_marked_best_effort(self):
        now = datetime(2026, 8, 11, 2, tzinfo=timezone.utc)
        state = {"last_successful_scan_end": "2026-08-01T00:00:00Z"}

        start, _, limited = self.collector.resolve_window(now, state, 72)

        self.assertEqual("2026-08-08T02:00:00Z", self.collector.format_timestamp(start))
        self.assertTrue(limited)


class ChannelPageFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.collector = load_collector()

    def test_page_entries_keep_relative_time_and_never_claim_exact_upload(self):
        initial_data = {
            "contents": [
                {
                    "lockupViewModel": {
                        "contentId": "fallback123",
                        "title": {"content": "Fresh 4X Gameplay"},
                        "metadata": ["321 views", "2 hours ago"],
                        "duration": "12:34",
                    }
                }
            ]
        }
        html = (
            "<html><script>var ytInitialData = "
            + json.dumps(initial_data)
            + ";</script></html>"
        ).encode()
        channel = {
            "id": "@fixture",
            "channel_id": "UCfixture",
            "name": "Fixture Channel",
            "weight": 10,
        }

        videos = self.collector.parse_channel_page(html, channel)

        self.assertEqual("fallback123", videos[0]["video_id"])
        self.assertEqual("channel_page", videos[0]["source_kind"])
        self.assertEqual("relative", videos[0]["time_precision"])
        self.assertEqual("2 hours ago", videos[0]["relative_published"])
        self.assertNotIn("published", videos[0])

    def test_effective_page_coverage_is_degraded_not_complete(self):
        channels = [
            {"channel_id": "UChigh", "weight": 10},
            {"channel_id": "UClow", "weight": 3},
        ]
        results = [
            {"channel_id": "UChigh", "status": "page_ok", "rss_status": "error"},
            {"channel_id": "UClow", "status": "rss_ok", "rss_status": "ok"},
        ]

        summary = self.collector.coverage_summary(channels, results)

        self.assertEqual("degraded", summary["status"])
        self.assertEqual(1.0, summary["effective_coverage"])
        self.assertEqual(0.5, summary["exact_coverage"])

    def test_missing_priority_channel_blocks_run(self):
        channels = [
            {"channel_id": "UChigh", "weight": 10},
            {"channel_id": "UClow", "weight": 3},
        ]
        results = [
            {"channel_id": "UChigh", "status": "error", "rss_status": "error"},
            {"channel_id": "UClow", "status": "rss_ok", "rss_status": "ok"},
        ]

        summary = self.collector.coverage_summary(channels, results)

        self.assertEqual("blocked", summary["status"])
        self.assertEqual(0.0, summary["priority_effective_coverage"])


if __name__ == "__main__":
    unittest.main()
