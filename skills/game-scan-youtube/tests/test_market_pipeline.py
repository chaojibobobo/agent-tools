"""Market scopes at the actual validate / commit / preview and runner entrypoints."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_intelligence_commit import write_workspace

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
DAY = "2026-08-11"


class MarketPipelineTests(unittest.TestCase):
    def workspace(self, include_ledger=True):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        write_workspace(root, include_ledger=include_ledger)
        (root / f"product-radar-input-{DAY}.json").write_text(json.dumps({
            "report_date": DAY, "focus_markets": ["MY", "JP"],
            "countries": [{"code": "MY"}, {"code": "JP"}],
        }))
        (root / "radar-markets.json").write_text('{"focus_markets":["CA"]}')
        return root

    def run_cli(self, script, root, *args):
        return subprocess.run([sys.executable, "-B", str(SCRIPTS/script), "--date", DAY,
                               "--dir", str(root), *args], text=True, capture_output=True)

    def test_planned_run_cannot_use_legacy_path_when_ledger_is_missing(self):
        for script, flags in (("validate_run.py", []), ("commit_state.py", []),
                              ("push_feishu.py", ["--dry-run"])):
            with self.subTest(script=script):
                root = self.workspace(include_ledger=False)
                before = (root / "seen_games.json").read_bytes()
                result = self.run_cli(script, root, *flags)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ledger", result.stderr)
                self.assertEqual(before, (root / "seen_games.json").read_bytes())

    def test_mismatched_ledger_rejected_at_all_three_entrypoints_without_state_writes(self):
        for script, flags in (("validate_run.py", ["--require-intelligence-ledger"]),
                              ("validate_run.py", []), ("push_feishu.py", ["--dry-run"]),
                              ("commit_state.py", [])):
            with self.subTest(script=script, flags=flags):
                root = self.workspace()
                before = {p.name:p.read_bytes() for p in root.glob("*.json")}
                result = self.run_cli(script, root, *flags)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("MY, JP", result.stderr)
                self.assertEqual(before, {p.name:p.read_bytes() for p in root.glob("*.json")})
                self.assertFalse((root/"receipts").exists())

    def test_one_run_override_survives_config_change_and_preserves_out_of_scope_lead(self):
        root = self.workspace()
        path = root / f"candidate-ledger-{DAY}.json"
        ledger = json.loads(path.read_text())
        run = ledger["product_radar_run"]
        run["focus_markets"] = ["MY","JP"]
        run["countries"] = [{"code": c, "status": "not_run", "source_urls": []} for c in run["focus_markets"]]
        obs = ledger["candidates"][0]["store_observations"][0]
        obs.update(country="US", store_url=obs["store_url"].replace("gl=MY","gl=US"),
                   region_source_url=obs["region_source_url"].replace("gl=MY","gl=US"))
        path.write_text(json.dumps(ledger))
        for script, args in (("validate_run.py", ["--require-intelligence-ledger"]),
                             ("push_feishu.py", ["--dry-run"]), ("commit_state.py", [])):
            result = self.run_cli(script, root, *args)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads((root/"scan_state.json").read_text())["last_successful_scan_end"])
        radar = json.loads((root/"product_radar.json").read_text())
        countries = {c["code"]:c for c in radar["countries"]}
        self.assertEqual({c for c, row in countries.items() if row["enabled"]}, {"MY","JP"})
        self.assertEqual(countries["US"]["metrics"]["runs_checked"], 0)
        self.assertEqual(countries["US"]["metrics"]["observations"], 1)

    def test_runner_passes_override_and_agent_gets_manifest_not_fixed_list(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            collector = root / "collector.py"
            collector.write_text("import json,sys\nfrom pathlib import Path\n"
                "Path(sys.argv[sys.argv.index('--output')+1]).write_text(json.dumps({'run_status':'RUN_OK',"
                "'source_health':{'succeeded_channels':1,'active_channels':1,'probe_status':'ok'}}))\n")
            fake_agent = root / "codex"
            fake_agent.write_text('#!/bin/sh\nprintf "%s" "$*" > agent-prompt.txt\nexit 42\n')
            fake_agent.chmod(0o700)
            result = subprocess.run(["bash", str(SCRIPTS/"run_scan.sh"), "--dir", td, "--date", DAY,
                                    "--markets", "MY,JP", "--agent", "codex", "--dry-run"],
                env={**os.environ, "GAME_SCAN_COLLECTOR":str(collector), "PATH":td+":"+os.environ["PATH"]},
                capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)  # fake agent deliberately stops before analysis
            self.assertTrue((root/"agent-prompt.txt").exists(), result.stdout+result.stderr)
            self.assertEqual(json.loads((root/f"product-radar-input-{DAY}.json").read_text())["focus_markets"], ["MY","JP"])
            prompt = (root/"agent-prompt.txt").read_text()
            self.assertIn("focus_markets", prompt)
            self.assertNotIn("exactly MY, ID, PH, GB, TH, CA", prompt)
            self.assertFalse((root/"receipts").exists())
