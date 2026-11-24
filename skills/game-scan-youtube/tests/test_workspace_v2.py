import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import sys


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from prepare_product_radar import build_query_manifest


FIXED_WORKSPACE = "/Users/bobo/Codexspace/tools/game-scan-youtube"


class WorkspaceInitializationTests(unittest.TestCase):
    def test_initializer_adds_new_states_without_overwriting_existing_files(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            existing_channels = {"sentinel": "preserve-me"}
            (root / "channels.json").write_text(json.dumps(existing_channels))

            result = subprocess.run(
                [
                    "/bin/bash",
                    str(SCRIPTS_DIR / "init_workspace.sh"),
                    "--dir",
                    str(root),
                    "--no-history",
                ],
                text=True,
                capture_output=True,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(existing_channels, json.loads((root / "channels.json").read_text()))
            for state_name in (
                "seen_games.json",
                "candidate_history.json",
                "product_radar.json",
                "scan_state.json",
            ):
                self.assertTrue((root / state_name).is_file(), state_name)


class RunnerContractTests(unittest.TestCase):
    def test_runner_requires_dual_radar_evidence_and_intelligence_ledger(self):
        script = (SCRIPTS_DIR / "run_scan.sh").read_text()

        self.assertIn('--state "$WORK_DIR/scan_state.json"', script)
        self.assertIn("--channel-page-fallback", script)
        self.assertIn("product-radar-input-$DATE.json", script)
        self.assertIn("candidate-ledger-$DATE.json", script)
        self.assertIn("--require-intelligence-ledger", script)

    def test_runtime_defaults_to_the_user_approved_fixed_workspace(self):
        for script_name in ("init_workspace.sh", "run_scan.sh", "run_daily_once.sh"):
            script = (SCRIPTS_DIR / script_name).read_text()
            self.assertIn(FIXED_WORKSPACE, script, script_name)
            self.assertNotIn("$HOME/.game-scan-youtube/workspace", script, script_name)


class ProductRadarManifestTests(unittest.TestCase):
    def test_google_play_is_primary_and_apple_is_secondary_for_every_country(self):
        manifest = build_query_manifest("2026-08-11")

        for country in manifest["countries"]:
            code = country["code"]
            primary = country["primary_source"]
            secondary = country["secondary_sources"]
            endpoints = primary["source_endpoints"]
            self.assertEqual("google_play", primary["store"])
            self.assertTrue(
                all("play.google.com/store/search" in endpoint for endpoint in endpoints),
                f"Non-Google source leaked into the primary radar for {code}",
            )
            self.assertTrue(
                all(f"gl={code}" in endpoint for endpoint in endpoints),
                f"Google Play endpoint is not localized for {code}",
            )
            self.assertEqual(["apple_app_store"], [item["store"] for item in secondary])
            self.assertTrue(
                all(
                    f"country={code}" in endpoint
                    for item in secondary
                    for endpoint in item["source_endpoints"]
                ),
                f"App Store endpoint is not localized for {code}",
            )

        self.assertTrue(manifest["rules"]["does_not_affect_channel_weight"])
        self.assertEqual("google_play", manifest["rules"]["primary_store"])
        self.assertTrue(manifest["rules"]["checked_requires_google_play_localized_source"])


if __name__ == "__main__":
    unittest.main()
