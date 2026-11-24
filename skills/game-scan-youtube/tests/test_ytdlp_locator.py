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
import youtube_transport as net

CRON_PATH = "/usr/bin:/bin"


class YtdlpLocatorTests(unittest.TestCase):
    def test_preflight_uses_dotenv_and_actual_launch_prefers_process_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / "yt dlp (workspace)"
            override = root / "yt dlp (process)"
            for binary in (local, override):
                binary.touch(mode=0o700)
            (root / ".env").write_text(f'YTDLP_BIN="{local}"\n')
            env = {"PATH": CRON_PATH}
            preflight = subprocess.run(
                [sys.executable, str(SCRIPTS / "collect_rss.py"), "--preflight-only",
                 "--channels", str(root / "channels.json"), "--feed-dir", tmp],
                env=env, check=True, capture_output=True, text=True)
            self.assertTrue(json.loads(preflight.stdout)["yt_dlp_available"])
            for env_value, expected in [(None, local), (str(override), override)]:
                with self.subTest(env_value=env_value), patch.dict(
                        os.environ, env if env_value is None else dict(env, YTDLP_BIN=env_value),
                        clear=True), patch.object(net.subprocess, "run") as run:
                    net.load_env(root)
                    run.return_value = subprocess.CompletedProcess([], 0, b'{"id":"fixture"}')
                    net.flat_playlist("UCfixture", 2)
                    self.assertEqual(str(expected), run.call_args.args[0][0])

    def test_implicit_lookup_covers_both_brew_prefixes_and_existing_path(self):
        for directory in ("/opt/homebrew/bin", "/usr/local/bin", "/bin"):
            with self.subTest(directory=directory), patch.dict(os.environ, {"PATH": CRON_PATH}, clear=True):
                def which(command, path=None):
                    target = f"{directory}/yt-dlp"
                    if command == target or (command == "yt-dlp" and
                                              directory in (path or os.environ["PATH"]).split(os.pathsep)):
                        return target
                    return None

                with patch.object(net.shutil, "which", side_effect=which), patch.object(net.subprocess, "run") as run:
                    run.return_value = subprocess.CompletedProcess([], 0, b'{"id":"fixture"}')
                    net.flat_playlist("UCfixture", 2)
                    self.assertEqual(f"{directory}/yt-dlp", run.call_args.args[0][0])

    def test_missing_binary_reason_survives_successful_channel_page_fallback(self):
        html = b'<script>var ytInitialData = {"contents":[{"lockupViewModel":{"contentId":"newvideo123","title":{"content":"Game"}}}]};</script>'
        with tempfile.TemporaryDirectory() as tmp:
            channels = Path(tmp) / "channels.json"
            channels.write_text(json.dumps({"seed_channels": [{"channel_id": "UCtest", "weight": 8}]}))
            responses = [(204, {}, b""), (429, {"retry-after": "60"}, b""), (200, {}, html)]
            with patch.dict(os.environ, {"PATH": CRON_PATH}, clear=True), \
                    patch.object(net.shutil, "which", return_value=None), \
                    patch.object(net, "request", side_effect=responses):
                result = collector.collect(channels, datetime.now(timezone.utc), 3, False, None, 6)
            channel = result["channel_results"][0]
            self.assertEqual("L3", channel["fetch_tier"])
            self.assertEqual("yt-dlp not found", channel["yt_dlp_error"])

    def test_bad_pin_is_reported_without_executing_a_different_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "yt-dlp").touch(mode=0o700)
            pinned = Path(tmp) / "non executable"
            pinned.touch(mode=0o600)
            with patch.dict(os.environ, {"PATH": tmp, "YTDLP_BIN": str(pinned)}, clear=True), \
                    patch.object(net.subprocess, "run") as run:
                with self.assertRaisesRegex(net.FetchError, "yt-dlp not found .*YTDLP_BIN"):
                    net.flat_playlist("UCfixture", 2)
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
