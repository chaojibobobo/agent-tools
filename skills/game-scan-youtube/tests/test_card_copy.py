"""Offline acceptance cases for the visible Decision Card, not its transport JSON."""
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import decision_card as card


def sample():
    game = {
        "name": "港城", "priority": "Focus", "card_evidence": "两频道拍到同一片联盟领地",
        "card_mechanism": "出港采矿→扩建船坞→派舰占港",
        "videos": [{"url": "https://www.youtube.com/watch?v=Abcdef_1234"}],
    }
    supplement = {
        "name": "雪港", "priority": "Track", "card_form": "挖矿+基地4X",
        "card_signal": "两频道实录", "videos": [{"url": "https://youtu.be/Zbcdef_1234"}],
    }
    return {"card_v3_required": True, "games": [game, supplement], "is_quiet": False}


class CardCopyTests(unittest.TestCase):
    def test_two_transports_use_one_budget_and_keep_distinct_video_sources(self):
        parsed = sample()
        day = date(2026, 11, 24)
        interactive = card.build_interactive_card(day, parsed, {})
        post = card.build_compact_post(day, parsed, {})
        for text in (card.visible_card_text(interactive), card.visible_post_text(post)):
            self.assertIn("【值得看】", text)
            self.assertIn("雪港｜挖矿+基地4X｜两频道实录｜YouTube 原视频", text)
            self.assertIn("今日无明显跨产品趋势", text)
            self.assertIn("时间存疑 0", text)
            self.assertIn("改名/更新 0", text)
            self.assertLessEqual(len(text), 500)
            self.assertLessEqual(len(text.splitlines()), 15)

    def test_all_banned_phrases_are_reported_with_field(self):
        self.assertGreaterEqual(len(card.BANNED_PHRASES), 15)
        for phrase in card.BANNED_PHRASES:
            for field, label, idx in (
                ("card_headline", "标题", None), ("card_evidence", "为什么看", 0),
                ("card_mechanism", "核心机制", 0), ("card_form", "补充前端", 1),
                ("card_signal", "补充信号", 1), ("card_trend", "趋势", None),
            ):
                with self.subTest(phrase=phrase, field=field):
                    parsed = sample()
                    dest = parsed if idx is None else parsed["games"][idx]
                    dest[field] = ("【值得看】" if field == "card_headline" else "") + phrase
                    with self.assertRaisesRegex(ValueError, label + ".*" + re.escape(phrase)):
                        card.validate_decision_copy(parsed)

    def test_skill_policy_drives_workspace_validation_but_not_ops_alerts(self):
        skill = Path(card.__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(skill / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
            work = root / "work"
            work.mkdir()
            day = "2099-01-02"
            for name in ("seen_games", "channels"):
                (work / f"{name}.json").write_text("{}")
            (work / f"{day}.md").write_text("> **Scan Profile:** 4X-SLG-STRICT-v1\n"
                                            "> **Card Headline:** 【今日可跳过】临时空话\n## Quiet Day\n")

            def run(script, *args):
                return subprocess.run([sys.executable, "-B", str(root / "scripts" / script),
                                       "--date", day, "--dir", str(work), *args], text=True, capture_output=True)

            source = (skill / "SKILL.md").read_text(encoding="utf-8")
            (root / "SKILL.md").write_text(source.replace("- 空泛评价：", "- 空泛评价：临时空话、"), encoding="utf-8")
            reject = run("validate_run.py")
            self.assertNotEqual(0, reject.returncode)
            self.assertRegex(reject.stderr, "标题.*临时空话")
            (root / "SKILL.md").write_text(source.replace("- 防御性废话：", "- 损坏字段："), encoding="utf-8")
            broken = run("validate_run.py")
            self.assertNotEqual(0, broken.returncode)
            self.assertRegex(broken.stderr, "SKILL.md.*防御性废话")
            (work / f"scan-input-{day}.json").write_text('{"run_status":"RUN_DEGRADED"}')
            alert = run("push_feishu.py", "--ops-alert", "--dry-run")
            self.assertEqual(0, alert.returncode, alert.stderr)
            self.assertIn("RUN_DEGRADED", alert.stdout)
            self.assertEqual(3, len(alert.stdout.strip().splitlines()))

    def test_rendering_does_not_hide_bad_copy_by_clipping_or_dropping_it(self):
        for field, limit in (("card_evidence", 40), ("card_mechanism", 45)):
            parsed = sample()
            parsed["games"][0][field] = "建" * (limit + 1)
            with self.assertRaisesRegex(ValueError, str(limit)):
                card.validate_decision_copy(parsed)
        parsed = sample()
        parsed["games"][0]["card_mechanism"] = "建港→采矿→装船→占岛"
        with self.assertRaisesRegex(ValueError, "3"):
            card.validate_decision_copy(parsed)
        for layout in ("行", "字"):
            parsed = sample()
            if layout == "行":
                for i in range(15):
                    other = deepcopy(parsed["games"][1])
                    other.update(name=f"游戏{i}", videos=[{"url": f"https://youtu.be/{i:011d}"}])
                    parsed["games"].append(other)
            else:
                parsed["games"][0]["name"] = "城" * 501
            with self.assertRaisesRegex(ValueError, layout):
                card.validate_decision_copy(parsed)

    def test_trend_requires_two_distinct_recent_named_products(self):
        parsed = sample()
        parsed.update(card_trend="两款都用采矿扩建船坞", trend_products=["港城", "港城"])
        with self.assertRaisesRegex(ValueError, "趋势"):
            card.validate_decision_copy(parsed)
        parsed["trend_products"] = ["港城", "过期"]
        with self.assertRaisesRegex(ValueError, "趋势"):
            card.validate_decision_copy(parsed)
        parsed["trend_products"] = ["港城", "旧港"]
        self.assertEqual(parsed["card_trend"], card.validate_decision_copy(parsed, {"hot_games": [{"name": "旧港"}]})["trend"])

    def test_rejects_invalid_youtube_url_even_when_a_report_has_a_video_list(self):
        for url in ("", "https://youtube.com.evil.test/watch?v=Abcdef_1234", "https://youtube.com/watch?list=abc",
                    "https://youtu.be/short", "https://youtube.com/watch?v=", "https://example.com/game"):
            parsed = sample()
            parsed["games"][1]["videos"] = [{"url": url}]
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "YouTube.*雪港"):
                card.validate_decision_copy(parsed)

    def test_renames_and_unverified_entries_are_not_new_discoveries(self):
        for marker, value in (("time_status", "unverified"), ("renamed_from", "旧港")):
            parsed = sample()
            parsed["games"][0][marker] = value
            with self.assertRaises(ValueError):
                card.validate_decision_copy(parsed)
        parsed = sample()
        parsed["games"][0].update(time_status="unverified", is_watchlist=True)
        parsed["games"][1].update(renamed_from="雪村", is_update=True)
        rendered = card.visible_card_text(card.build_interactive_card(date.today(), parsed, {}))
        self.assertIn("时间存疑 1", rendered)
        self.assertIn("改名/更新 1", rendered)

    def test_degraded_data_cannot_render_a_normal_card(self):
        with self.assertRaisesRegex(ValueError, "RUN_DEGRADED"):
            card.validate_decision_copy({"run_status": "RUN_DEGRADED", "games": [], "is_quiet": True})

    def test_new_reports_do_not_reuse_one_game_or_video_as_two_products(self):
        for key in ("name", "videos"):
            parsed = sample()
            parsed["games"][1][key] = parsed["games"][0][key]
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "重复"):
                card.validate_decision_copy(parsed)

    def test_archive_budget_preview_is_not_a_general_bypass(self):
        parsed = sample()
        with self.assertRaises(ValueError):
            card.historical_preview(parsed, card.CardBudgetError("旧标题超限"))
        old = {"games": [{"name": "A", "videos": [{"url": "https://youtu.be/Abcdef_1234"}]}]}
        with self.assertRaises(ValueError):
            card.historical_preview(old, ValueError("invalid source"))
        self.assertIn("https://youtu.be/Abcdef_1234", card.historical_preview(old, card.CardBudgetError("35")))
