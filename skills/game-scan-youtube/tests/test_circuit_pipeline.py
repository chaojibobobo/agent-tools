"""Offline integration checks for every entry that can publish or mark completion."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import commit_state
import push_feishu
from test_intelligence_commit import write_workspace
from test_intelligence_state import candidate as fixture_candidate

DAY = "2099-01-05"


def scan(l0=False):
    count = 0 if l0 else 2
    return {
        "run_status": "RUN_DEGRADED" if l0 else "RUN_OK",  # independently check raw coverage
        "source_health": {
            "probe_status": "failed" if l0 else "ok",
            "active_channels": 5, "succeeded_channels": count, "failed_channels": 5-count,
            "healthy_rate": count / 5, "healthy_threshold": 0.6,
            "degraded_reason": "L0 timeout" if l0 else "L1/L2/L2.5/L3 below threshold",
        },
        "source_summary": {"status": "degraded"},
    }


class CircuitPipelineTests(unittest.TestCase):
    def test_direct_commit_never_modifies_any_cursor_or_ledger_on_outage(self):
        for l0 in (False, True):
            with self.subTest(l0=l0), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                cursor = root / "scan_state.json"
                cursor.write_text('{"last_successful_scan_end":"previous"}')
                (root / f"scan-input-{DAY}.json").write_text(json.dumps(scan(l0)))
                before = {p: p.read_bytes() for p in root.iterdir()}
                with self.assertRaisesRegex(ValueError, "RUN_DEGRADED"):
                    commit_state.commit(DAY, root)
                self.assertEqual(before, {p: p.read_bytes() for p in root.iterdir()})

    def test_ops_dry_run_needs_no_report_no_credentials_and_produces_no_receipt(self):
        for l0 in (False, True):
            with self.subTest(l0=l0), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / f"scan-input-{DAY}.json").write_text(json.dumps(scan(l0)))
                cmd = [sys.executable, str(SCRIPTS / "push_feishu.py"), "--dir", td,
                       "--date", DAY, "--dry-run"]
                result = subprocess.run(cmd, env={"HOME": td, "PATH": os.environ["PATH"]},
                                        capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr + result.stdout)
                lines = result.stdout.strip().splitlines()
                self.assertLessEqual(len(lines), 3)
                self.assertEqual("【数据降级：仅覆盖 %d/5 频道】" % (0 if l0 else 2), lines[0])
                self.assertIn("RUN_DEGRADED", result.stdout)
                self.assertIn("L0" if l0 else "L2", result.stdout)
                self.assertIn("排查", result.stdout)
                self.assertNotIn("今日无新增", result.stdout)
                self.assertFalse((root / "receipts").exists())

    def test_real_ops_alert_sends_only_one_message_and_never_a_daily_receipt(self):
        sender = Mock(return_value={"code": 0, "data": {"message_id": "om_test"}})
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / f"scan-input-{DAY}.json").write_text(json.dumps(scan(True)))
            with patch.object(sys, "argv", ["push", "--dir", td, "--date", DAY]), \
                 patch.dict(os.environ, {"FEISHU_APP_ID": "fixture", "FEISHU_APP_SECRET": "fixture",
                                         "FEISHU_USER_OPEN_ID": "ou_fixture"}), \
                 patch.object(push_feishu, "get_token", return_value="fixture"), \
                 patch.object(push_feishu, "post_json", sender), contextlib.redirect_stdout(io.StringIO()):
                push_feishu.main()
            self.assertEqual(1, sender.call_count)
            payload = sender.call_args.args[1]
            self.assertEqual("text", payload["msg_type"])
            self.assertLessEqual(len(json.loads(payload["content"])["text"].splitlines()), 3)
            self.assertFalse((root / "receipts" / f"{DAY}.feishu.json").exists())

    def test_cron_runs_full_collection_on_retry_and_never_stamps_degraded(self):
        # The mock collector writes real scan input then exits like a bounded source failure.
        # Even an old report/receipt on attempt 2 must not send a normal message or skip collection.
        for l0 in (False, True):
            with self.subTest(l0=l0), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                work = root / "work"
                collector = root / "collector.py"
                collector.write_text(
                    "import sys\nfrom pathlib import Path\n"
                    "out=Path(sys.argv[sys.argv.index('--output')+1])\n"
                    "with (out.parent/'attempts').open('a') as f: f.write('1\\n')\n"
                    f"out.write_text({json.dumps(scan(l0))!r})\nsys.exit(1)\n")
                env = {"HOME": td, "PATH": os.environ["PATH"], "GAME_SCAN_COLLECTOR": str(collector)}
                for attempt in range(2):
                    res = subprocess.run(["bash", str(SCRIPTS / "run_daily_once.sh"), "--date", DAY,
                                          "--dir", str(work)], env=env, text=True, capture_output=True)
                    self.assertEqual(75, res.returncode, res.stdout + res.stderr)
                    self.assertFalse((work / ".run-stamps" / f"{DAY}.done").exists())
                    self.assertEqual(attempt + 1, len((work / "attempts").read_text().splitlines()))
                    self.assertIsNone(json.loads((work / "scan_state.json").read_text())["last_successful_scan_end"])
                    self.assertTrue((work / f"degraded-{DAY}.md").read_text().startswith("【数据降级：仅覆盖 "))
                    (work / f"{DAY}.md").write_text("## Quiet Day\n")
                    (work / "receipts").mkdir(exist_ok=True)
                    (work / "receipts" / f"{DAY}.feishu.json").write_text(json.dumps(
                        {"date": DAY, "status": "sent", "message_id": "stale"}))

    def test_parser_preserves_copy_fields_and_separate_unverified_section(self):
        with tempfile.TemporaryDirectory() as td:
            report = Path(td) / "report.md"
            report.write_text(
                "> **Card Headline:** 【常规跟踪】2款调整城建节奏\n"
                "> **Card Trend:** 采石回城扩建\n> **Trend Products:** A｜B\n"
                "## 待核实发现（时间存疑）\n### A — 测试\n"
                "- [实机](https://www.youtube.com/watch?v=abcdefghi12)\n"
                "**Time Status:** unverified\n**Card Evidence:** 矿车运石进城\n"
                "**Card Mechanism:** 运石→扩仓\n**Card Form:** 矿车+城建\n**Card Signal:** 可扩仓\n")
            parsed = push_feishu.parse_report(report)
            self.assertEqual(["A", "B"], parsed["trend_products"])
            self.assertEqual("采石回城扩建", parsed["card_trend"])
            game = parsed["games"][0]
            self.assertTrue(game["is_watchlist"] and game["is_time_unverified"])
            self.assertEqual("unverified", game["time_status"])
            self.assertEqual("运石→扩仓", game["card_mechanism"])

    def test_force_push_dry_run_and_missing_credentials_never_claim_delivery(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "work"
            work.mkdir()
            (work / f"{DAY}.md").write_text(
                "> **Scan Profile:** 4X-SLG-STRICT-v1\n## Quiet Day\n")
            cmd = ["bash", str(SCRIPTS / "run_scan.sh"), "--dir", str(work), "--date", DAY, "--force-push"]
            env = {"HOME": td, "PATH": os.environ["PATH"]}
            preview = subprocess.run(cmd + ["--dry-run"], env=env, text=True, capture_output=True)
            self.assertEqual(0, preview.returncode, preview.stderr + preview.stdout)
            self.assertIn("DRY RUN", preview.stdout)
            local = subprocess.run(cmd, env=env, text=True, capture_output=True)
            self.assertEqual(0, local.returncode, local.stdout + local.stderr)
            self.assertIn("未发送", local.stderr)
            self.assertFalse((work / "receipts").exists())
            self.assertFalse((work / ".run-stamps" / f"{DAY}.done").exists())

    def test_chinese_renames_are_merged_and_not_reported_as_new(self):
        day = "2026-08-11"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_workspace(root)
            path = root / f"candidate-ledger-{day}.json"
            data = json.loads(path.read_text())
            candidate = data["candidates"][0]
            original, alias = "山海王国", "山海战纪"
            candidate.update(canonical_name=alias, aliases=[], package_ids=["com.fixture.newpkg"],
                             renamed_from=original, rename_evidence="发行方相同，联盟菜单与主城图标一致")
            path.write_text(json.dumps(data))
            report = root / f"{day}.md"
            report.write_text(report.read_text().replace("### Last Throne", "### " + alias))
            seen_path = root / "seen_games.json"
            seen = json.loads(seen_path.read_text())
            old = seen["games"].pop("Last King")
            old.update(canonical_name=original)
            seen["games"] = {"完全不相关的游戏": {"seen_videos": []}, original: old}
            seen_path.write_text(json.dumps(seen))
            result = commit_state.commit(day, root)
            updated = json.loads(seen_path.read_text())["games"]
            self.assertEqual(0, result["new_games"])
            self.assertNotIn(alias, updated)
            self.assertIn(alias, updated[original]["aliases"])
            self.assertEqual(f"疑似更名/换皮自：{original}（依据：{candidate['rename_evidence']}）",
                             updated[original]["rename_note"])
            self.assertFalse(updated["完全不相关的游戏"]["seen_videos"])

    def test_pending_day_one_promotes_to_core_once_without_polluting_seen_games(self):
        name = "前线城堡"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_workspace(root)
            ledger = json.loads((root / "candidate-ledger-2026-08-11.json").read_text())
            for i, day in enumerate(("2026-08-11", "2026-08-12")):
                kind = ("Pending", "Core")[i]
                cid = ("pending_vid", "core_video1")[i]
                published = day + "T01:00:00Z"
                candidate = fixture_candidate(name, kind, video_id=cid, published=published)
                candidate["package_ids"] = ["com.fixture.frontier"]
                source = candidate["videos"][0]["url"]
                ledger["candidates"] = [candidate]
                (root / f"candidate-ledger-{day}.json").write_text(json.dumps(ledger))
                (root / f"scan-input-{day}.json").write_text(json.dumps({
                    "run_status": "RUN_OK", "source_summary": {"status": "complete"},
                    "source_health": {"active_channels": 1, "succeeded_channels": 1, "healthy_rate": 1},
                    "window": {"start": day + "T00:00:00Z", "end": day + "T02:00:00Z"},
                    "videos": [{"video_id": cid, "time_status": "verified", "published": published}],
                }))
                section = ("Watchlist", "New Games")[i]
                (root / f"{day}.md").write_text(
                    f"> **Scan Profile:** 4X-SLG-STRICT-v1\n## {section}\n### {name} — 测试\n"
                    f"- [实机]({source})\n**4X Fit:** {kind}\n"
                    "**Card Evidence:** 盟军集结占领据点\n**Card Mechanism:** 征兵→出城→占点\n")
                result = commit_state.commit(day, root)
                seen = json.loads((root / "seen_games.json").read_text())["games"]
                tracked = json.loads((root / "candidate_history.json").read_text())["candidates"]
                self.assertEqual(kind, tracked["pkg:com.fixture.frontier"]["classification"])
                self.assertEqual(i, result["new_games"])
                if not i:
                    self.assertNotIn(name, seen)
                else:
                    self.assertEqual(day, seen[name]["first_seen"])
                    self.assertEqual(0, commit_state.commit(day, root)["new_games"])


if __name__ == "__main__":
    unittest.main()
