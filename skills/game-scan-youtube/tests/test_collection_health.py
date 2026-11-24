import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import collect_rss as collector

NOW = datetime(2026, 8, 12, 8, tzinfo=timezone.utc)
FEED = b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry><yt:videoId>newvideo123</yt:videoId><title>A</title><published>2026-08-12T07:00:00Z</published></entry></feed>'


class CollectionHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.channels = self.root / "channels.json"
        self.set_channels(1)
        self.env = patch.dict(os.environ, {"YOUTUBE_API_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def set_channels(self, count):
        self.channels.write_text(json.dumps({"seed_channels": [
            {"channel_id": f"UCtest{n}", "weight": 8, "name": f"Test {n}"}
            for n in range(count)
        ]}))

    def scan(self, **kw):
        return collector.collect(self.channels, NOW, 3, False, None, 6, **kw)

    def test_one_failed_probe_prevents_all_channel_calls(self):
        self.set_channels(5)
        with patch.object(collector.net, "request", return_value=(503, {}, b"")) as req:
            result = self.scan()
        self.assertEqual(1, req.call_count)
        self.assertLessEqual(req.call_args.args[1], 5)
        self.assertEqual("RUN_DEGRADED", result["run_status"])
        self.assertIn("L0", result["source_health"]["degraded_reason"])
        self.assertEqual(5, result["source_health"]["failed_channels"])

    def test_healthy_rate_threshold_and_equality(self):
        self.set_channels(5)
        def fetch(url, timeout, **kwargs):
            if "generate_204" in url:
                return 204, {}, b""
            return (200, {}, FEED) if url.endswith(("0", "1")) else (404, {}, b"")
        with patch.object(collector.net, "request", side_effect=fetch):
            blocked = self.scan()
            okay = self.scan(health_threshold=.4)
        self.assertEqual(.4, blocked["source_health"]["healthy_rate"])
        self.assertEqual("RUN_DEGRADED", blocked["run_status"])
        self.assertEqual("RUN_OK", okay["run_status"])

    def test_playlist_uploads_endpoint_and_only_unseen_detail_ids(self):
        os.environ["YOUTUBE_API_KEY"] = "fixture"
        (self.root / "seen_games.json").write_text(json.dumps({"games": {"known": {
            "seen_videos": ["https://www.youtube.com/watch?v=oldvideo123"]}}}))
        def fetch(url, timeout, **kwargs):
            if "generate_204" in url:
                return 204, {}, b""
            if "playlistItems" in url:
                self.assertIn("playlistId=UUtest0", url)
                self.assertNotIn("fixture", url)
                items = [{"contentDetails": {"videoId": vid, "videoPublishedAt": "2026-08-12T07:00:00Z"}, "snippet": {"title": "Game"}}
                         for vid in ["newvideo123", "oldvideo123"]]
                return 200, {}, json.dumps({"items": items}).encode()
            self.assertIn("/videos?", url)
            self.assertIn("newvideo123", url)
            self.assertNotIn("oldvideo123", url)
            return 200, {}, b'{"items":[{"id":"newvideo123","contentDetails":{"duration":"PT2M"}}]}'
        with patch.object(collector.net, "request", side_effect=fetch) as req:
            result = self.scan()
        self.assertEqual(3, req.call_count)
        self.assertEqual(1, result["source_health"]["fetch_tier_breakdown"]["L1"])
        self.assertEqual("verified", result["videos"][0]["time_status"])
        self.assertEqual("PT2M", result["videos"][0]["duration"])
        # Before publication/commit the video is still evidence, but its details
        # were already fetched and must not consume another data API request.
        with patch.object(collector.net, "request", side_effect=fetch) as req:
            rerun = self.scan()
        self.assertEqual(2, req.call_count)
        self.assertEqual("PT2M", rerun["videos"][0]["duration"])

    def test_quota_circuit_skips_api_for_remaining_channels_but_still_collects_rss(self):
        self.set_channels(3)
        os.environ["YOUTUBE_API_KEY"] = "fixture"
        calls = []
        def fetch(url, timeout, **kwargs):
            calls.append(url)
            if "generate_204" in url:
                return 204, {}, b""
            if "playlistItems" in url:
                return 403, {}, b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}'
            return 200, {}, FEED
        with patch.object(collector.net, "request", side_effect=fetch):
            result = self.scan(workers=1)
        self.assertEqual(1, sum("playlistItems" in url for url in calls))
        self.assertEqual(3, result["source_health"]["fetch_tier_breakdown"]["L2"])
        self.assertEqual("RUN_OK", result["run_status"])

    def test_missing_exact_upload_time_is_not_fabricated(self):
        for timestamp in [b"2026-08-12", b"", b"2026-08-12T07:00:00"]:
            with self.subTest(timestamp=timestamp):
                item = list(collector.parse_entries(
                    FEED.replace(b"2026-08-12T07:00:00Z", timestamp), {"weight": 8}))[0]
                self.assertEqual("unverified", item["time_status"])
                self.assertNotIn("published", item)

    def test_exponential_retries_are_bounded(self):
        statuses = iter([(204, {}, b""), (503, {}, b""), (503, {}, b""), (200, {}, FEED)])
        with patch.object(collector.net, "request", side_effect=lambda *a, **kw: next(statuses)), \
                patch.object(collector.time, "sleep") as delay:
            result = self.scan(retries=2)
        self.assertEqual([.25, .5], [c.args[0] for c in delay.call_args_list])
        self.assertEqual("RUN_OK", result["run_status"])

    def test_precursor_pending_ids_can_be_verified_not_counted_or_enriched_as_new(self):
        (self.root / "candidate_history.json").write_text(json.dumps({"candidates": {
            "known": {"pending_video_ids": ["newvideo123"]}, "legacy": {}}}))
        cursor = {"last_successful_scan_end": "2026-08-12T07:30:00Z"}
        extra = b"<entry><yt:videoId>otherold123</yt:videoId><published>2026-08-12T07:00:00Z</published></entry>"
        dated_feed = FEED.replace(b"</feed>", extra + b"</feed>")
        api_items = [{"contentDetails": {"videoId": vid, "videoPublishedAt": "2026-08-12T07:00:00Z"}}
                     for vid in ("newvideo123", "otherold123")]
        for tier in ("L1", "L2"):
            with self.subTest(tier=tier):
                os.environ["YOUTUBE_API_KEY"] = "fixture" if tier == "L1" else ""
                body = json.dumps({"items": api_items}).encode() if tier == "L1" else dated_feed
                with patch.object(collector.net, "request", side_effect=[(204, {}, b""), (200, {}, body)]) as req:
                    result = self.scan(state=cursor)
                self.assertEqual(["newvideo123"], [v["video_id"] for v in result["videos"]])
                recovered = result["videos"][0]
                self.assertEqual(("verified", "2026-08-12T07:00:00Z", True),
                                 (recovered["time_status"], recovered["published"], recovered["verification_update"]))
                self.assertEqual(0, result["channel_results"][0]["videos_in_window"])
                self.assertEqual(2, req.call_count)  # probe + one channel request; no targeted lookup

    def test_conditional_304_replays_body_even_after_failed_run(self):
        self.set_channels(2)
        headers = {"etag": '"e1"', "last-modified": "Mon, 10 Aug 2026 08:00:00 GMT"}
        def first(url, timeout, **kwargs):
            if "generate_204" in url:
                return 204, {}, b""
            return (200, headers, FEED) if url.endswith("0") else (404, {}, b"")
        with patch.object(collector.net, "request", side_effect=first):
            self.assertEqual("RUN_DEGRADED", self.scan()["run_status"])
        def second(url, timeout, **kwargs):
            if "generate_204" in url:
                return 204, {}, b""
            if url.endswith("0"):
                self.assertEqual('"e1"', kwargs["headers"]["If-None-Match"])
                self.assertIn("If-Modified-Since", kwargs["headers"])
                return 304, {}, b""
            return 200, {}, FEED
        with patch.object(collector.net, "request", side_effect=second):
            result = self.scan()
        self.assertEqual("RUN_OK", result["run_status"])
        self.assertEqual(["newvideo123"], [v["video_id"] for v in result["videos"]])
        self.assertEqual("verified", result["videos"][0]["time_status"])
        self.assertEqual("not_modified", result["channel_results"][0]["rss_status"])

    def test_429_flat_playlist_then_page_are_unverified(self):
        html = b'<script>var ytInitialData = {"contents":[{"lockupViewModel":{"contentId":"newvideo123","title":{"content":"Game"}}}]};</script>'
        def fetch(url, timeout, **kwargs):
            if "generate_204" in url:
                return 204, {}, b""
            return (429, {}, b"") if "feeds/" in url else (200, {}, html)
        flat = subprocess.CompletedProcess([], 0, b'{"id":"newvideo123","title":"Game","upload_date":"20260812"}\n', b"")
        with patch.object(collector.net, "request", side_effect=fetch), patch.object(collector.net.shutil, "which", return_value="/test/yt-dlp"), patch.object(collector.net.subprocess, "run", return_value=flat) as cmd:
            result = self.scan()
        self.assertIn("--flat-playlist", cmd.call_args.args[0])
        self.assertIn("--dump-json", cmd.call_args.args[0])
        self.assertEqual(1, result["source_health"]["fetch_tier_breakdown"]["L2.5"])
        self.assertEqual("unverified", result["videos"][0]["time_status"])
        with patch.object(collector.net, "request", side_effect=fetch), patch.object(collector.net.shutil, "which", return_value=None):
            result = self.scan()
        self.assertEqual(1, result["source_health"]["fetch_tier_breakdown"]["L3"])
        self.assertEqual("unverified", result["videos"][0]["time_status"])

    def test_workspace_env_never_executes_or_overwrites_parent(self):
        (self.root / ".env").write_text("export YOUTUBE_API_KEY='local key'\nHTTPS_PROXY=https://from-file\nEVIL=$(touch should-not-exist)\n")
        with patch.dict(os.environ, {"HTTPS_PROXY": "from-parent"}, clear=True):
            collector.net.load_env(self.root)
            self.assertEqual("local key", os.environ["YOUTUBE_API_KEY"])
            self.assertEqual("from-parent", os.environ["HTTPS_PROXY"])
            self.assertNotIn("EVIL", os.environ)


if __name__ == "__main__":
    unittest.main()
