import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"


def load_script_module(name: str):
    path = SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def decision_game(
    name,
    hook,
    *,
    priority="",
    cross_ref="1 channel",
    videos=None,
    watchlist=False,
    update=False,
    gameplay="",
):
    return {
        "name": name,
        "hook": hook,
        "is_update": update,
        "is_watchlist": watchlist,
        "videos": videos or [],
        "status": "Early Access",
        "developer": "Fixture Dev",
        "platform": "Android",
        "gameplay": gameplay,
        "download_url": "https://play.google.com/fixture",
        "store_signal": "new test",
        "cross_ref": cross_ref,
        "priority": priority,
    }


def decision_fixture():
    long_videos = [
        {
            "title": f"Gameplay {index}",
            "url": f"https://www.youtube.com/watch?v=focus{index}",
            "meta": f"Channel {index} · {10 + index}:15 · 100 views",
        }
        for index in range(1, 4)
    ]
    return {
        "is_quiet": False,
        "takeaways": [
            "成熟 IP 正在把局内 build 接回可见的局外资产。",
            "小体量策略正在减少操作，放大不可逆取舍。",
        ],
        "games": [
            decision_game(
                "Explicit Focus",
                "成熟 IP 用 Roguelike TD + 轻基地扩展局外成长",
                priority="Focus",
                cross_ref="3 channels",
                videos=long_videos,
            ),
            decision_game(
                "Implicit Focus",
                "four-channel strategy launch",
                cross_ref="4 channels",
                videos=[long_videos[0]],
            ),
            decision_game(
                "Track Game",
                "single-channel TD",
                priority="Track",
                videos=[long_videos[0]],
                gameplay="low gameplay detail should be compacted",
            ),
            decision_game(
                "Watch Candidate",
                "duration unverified",
                watchlist=True,
                videos=[long_videos[0]],
            ),
            decision_game(
                "Known Game",
                "update",
                update=True,
                videos=[long_videos[0]],
            ),
        ],
    }


class DecisionCardModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.card = load_script_module("decision_card")

    def test_model_answers_value_then_priority(self):
        model = self.card.build_decision_model(decision_fixture())

        self.assertEqual("今日值得看｜Explicit Focus", model["headline"])
        self.assertEqual(["Explicit Focus"], [g["name"] for g in model["focus"]])
        self.assertEqual(
            ["Implicit Focus", "Track Game"],
            [g["name"] for g in model["supplemental"]],
        )
        self.assertEqual("1 必看 · 2 补充 · 1 观察 · 1 更新", model["counts"])
        self.assertLessEqual(len(model["conclusion"]), 80)
        self.assertLessEqual(len(model["trend"]), 60)

    def test_focus_evidence_prefers_cross_channel_and_duration(self):
        model = self.card.build_decision_model(decision_fixture())
        focus = model["focus"][0]

        self.assertEqual("3 频道同时覆盖 · 3 条视频全部 ≥10 分钟", focus["why"])
        self.assertEqual("Roguelike TD → 轻基地扩展局外成长", focus["mechanism"])

    def test_two_explicit_focus_games_expand_but_never_more_than_two(self):
        parsed = decision_fixture()
        parsed["games"][1]["priority"] = "Focus"
        parsed["games"][2]["priority"] = "Focus"

        model = self.card.build_decision_model(parsed)

        self.assertEqual(2, len(model["focus"]))
        self.assertEqual(
            ["Implicit Focus", "Explicit Focus"],
            [game["name"] for game in model["focus"]],
        )


class ReportParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.push_feishu = load_script_module("push_feishu")

    def write_report(self, directory: Path, text: str) -> Path:
        report = directory / "2026-07-25.md"
        report.write_text(text)
        return report

    def test_known_game_new_video_is_not_misclassified_as_new_game(self):
        """Catches the generic dash matcher stealing the known-update branch."""
        with tempfile.TemporaryDirectory() as raw_dir:
            report = self.write_report(
                Path(raw_dir),
                """# Game Scout — 2026-07-25

## New Videos for Known Games

### Kingdom Fall — new video
- [New base defense run](https://www.youtube.com/watch?v=known1) — Cute Games · 12:34
""",
            )

            parsed = self.push_feishu.parse_report(report)

        self.assertEqual(1, len(parsed["games"]))
        self.assertEqual("Kingdom Fall", parsed["games"][0]["name"])
        self.assertTrue(parsed["games"][0]["is_update"])

    def test_ascii_double_dash_report_is_parseable(self):
        """Catches reports produced with -- instead of an em dash."""
        with tempfile.TemporaryDirectory() as raw_dir:
            report = self.write_report(
                Path(raw_dir),
                """# Game Scout -- 2026-07-25

## New Games

### 03:10 upload | Dragonfall Kingdom -- 4X launch
- [Gameplay Android](https://www.youtube.com/watch?v=new1) -- AetherFyre · 11:20
""",
            )

            parsed = self.push_feishu.parse_report(report)

        self.assertEqual("Dragonfall Kingdom", parsed["games"][0]["name"])
        self.assertEqual("4X launch", parsed["games"][0]["hook"])
        self.assertEqual("https://www.youtube.com/watch?v=new1", parsed["games"][0]["videos"][0]["url"])

    def test_non_quiet_report_with_zero_parsed_games_is_rejected(self):
        """Catches silent empty Feishu messages when report syntax drifts."""
        with tempfile.TemporaryDirectory() as raw_dir:
            report = self.write_report(
                Path(raw_dir),
                "# Game Scout — 2026-07-25\n\n## New Games\n\nUnparseable body.\n",
            )

            with self.assertRaises(ValueError):
                self.push_feishu.parse_report(report)

    def test_watchlist_entry_is_not_counted_as_confirmed_new_game(self):
        """Catches low-evidence watchlist items inflating the new-game count."""
        with tempfile.TemporaryDirectory() as raw_dir:
            report = self.write_report(
                Path(raw_dir),
                """# Game Scout — 2026-07-25

## Watchlist

### Candidate Game — duration unverified
- [Candidate video](https://www.youtube.com/watch?v=watch1) — Channel
""",
            )

            parsed = self.push_feishu.parse_report(report)

        self.assertTrue(parsed["games"][0]["is_watchlist"])

    def test_report_takeaways_are_available_to_the_feishu_renderer(self):
        """Catches the report's strategic conclusions being dropped from Feishu."""
        with tempfile.TemporaryDirectory() as raw_dir:
            report = self.write_report(
                Path(raw_dir),
                """# Game Scout — 2026-07-25

## New Games

### Focus Game — strong signal
- [Gameplay](https://www.youtube.com/watch?v=focus1) — Channel

## What They Are Playing

1. **重建经营**正在成为共同入口。
2. TD 正在分化为改地图与快 PvP。
""",
            )

            parsed = self.push_feishu.parse_report(report)

        self.assertEqual(
            ["重建经营正在成为共同入口。", "TD 正在分化为改地图与快 PvP。"],
            parsed["takeaways"],
        )


class FeishuExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.push_feishu = load_script_module("push_feishu")

    def test_multichannel_findings_lead_and_single_channel_findings_are_compact(self):
        """Catches every discovery being expanded equally regardless of signal strength."""
        parsed = {
            "is_quiet": False,
            "takeaways": ["重建经营是今日共同机制。"],
            "games": [
                {
                    "name": "High Signal",
                    "hook": "4-channel launch",
                    "is_update": False,
                    "is_watchlist": False,
                    "videos": [
                        {
                            "title": "High gameplay",
                            "url": "https://www.youtube.com/watch?v=high1",
                            "meta": "",
                        }
                    ],
                    "status": "Early Access",
                    "developer": "High Dev",
                    "platform": "Android",
                    "gameplay": "high gameplay detail",
                    "download_url": "https://play.google.com/high",
                    "store_signal": "4-channel strong signal",
                    "cross_ref": "4 channels",
                    "priority": "",
                },
                {
                    "name": "Low Signal",
                    "hook": "single-channel TD",
                    "is_update": False,
                    "is_watchlist": False,
                    "videos": [
                        {
                            "title": "Low gameplay",
                            "url": "https://www.youtube.com/watch?v=low1",
                            "meta": "",
                        }
                    ],
                    "status": "New",
                    "developer": "Low Dev",
                    "platform": "Android",
                    "gameplay": "low gameplay detail should be compacted",
                    "download_url": "",
                    "store_signal": "single channel",
                    "cross_ref": "1 channel",
                    "priority": "",
                },
                {
                    "name": "Watch Candidate",
                    "hook": "duration unverified",
                    "is_update": False,
                    "is_watchlist": True,
                    "videos": [
                        {
                            "title": "Watch video",
                            "url": "https://www.youtube.com/watch?v=watch1",
                            "meta": "",
                        }
                    ],
                    "status": "New",
                    "developer": "",
                    "platform": "",
                    "gameplay": "",
                    "download_url": "",
                    "store_signal": "",
                    "cross_ref": "1 channel",
                    "priority": "",
                },
                {
                    "name": "Known Game",
                    "hook": "update",
                    "is_update": True,
                    "is_watchlist": False,
                    "videos": [
                        {
                            "title": "Update video",
                            "url": "https://www.youtube.com/watch?v=update1",
                            "meta": "",
                        }
                    ],
                    "status": "",
                    "developer": "",
                    "platform": "",
                    "gameplay": "",
                    "download_url": "",
                    "store_signal": "",
                    "cross_ref": "",
                    "priority": "",
                },
            ],
        }

        content = self.push_feishu.build_feishu_content(
            date(2026, 7, 25),
            parsed,
            {"period": "", "total_games": 0, "total_channels": 0, "hot_games": []},
        )
        title = content["zh_cn"]["title"]
        text = "\n".join(
            item.get("text", "")
            for group in content["zh_cn"]["content"]
            for item in group
        )

        self.assertEqual(
            "Game Scout 07-25｜1 重点 · 1 补充 · 1 观察 · 1 更新",
            title,
        )
        self.assertLess(text.index("今日判断"), text.index("先看"))
        self.assertLess(text.index("先看"), text.index("其他新增"))
        self.assertLess(text.index("其他新增"), text.index("观察与更新"))
        self.assertIn("high gameplay detail", text)
        self.assertNotIn("low gameplay detail should be compacted", text)
        self.assertIn("1 频道", text)
        self.assertNotIn("1 channel", text)


class CollectorTests(unittest.TestCase):
    def test_fixture_feed_is_filtered_to_exact_24_hour_window(self):
        """Catches collectors that include stale entries or depend on live network in tests."""
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            channels = root / "channels.json"
            feeds = root / "feeds"
            output = root / "scan-input.json"
            feeds.mkdir()
            channels.write_text(
                json.dumps(
                    {
                        "seed_channels": [
                            {
                                "id": "@fixture",
                                "channel_id": "UCfixture",
                                "name": "Fixture Channel",
                                "weight": 10,
                            }
                        ],
                        "discovered_channels": [],
                    }
                )
            )
            (feeds / "UCfixture.xml").write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/">
  <entry>
    <yt:videoId>fresh123</yt:videoId>
    <title>Fresh Mobile Strategy Gameplay Android</title>
    <published>2026-07-25T11:00:00+00:00</published>
    <media:group>
      <media:description>4X base building gameplay</media:description>
      <media:community><media:statistics views="123"/></media:community>
    </media:group>
  </entry>
  <entry>
    <yt:videoId>stale123</yt:videoId>
    <title>Old Mobile Strategy Gameplay Android</title>
    <published>2026-07-24T11:59:59+00:00</published>
  </entry>
</feed>
"""
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "collect_rss.py"),
                    "--channels",
                    str(channels),
                    "--output",
                    str(output),
                    "--feed-dir",
                    str(feeds),
                    "--now",
                    "2026-07-25T12:00:00Z",
                ],
                text=True,
                capture_output=True,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            data = json.loads(output.read_text())
            self.assertEqual(["fresh123"], [video["video_id"] for video in data["videos"]])
            self.assertEqual(1, data["source_summary"]["channels_succeeded"])


class CompletionGateTests(unittest.TestCase):
    def test_daily_wrapper_does_not_stamp_child_exit_zero_without_evidence(self):
        """Catches the false-completion bug where exit 0 alone writes a done stamp."""
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_codex = fake_bin / "codex"
            fake_codex.write_text("#!/usr/bin/env bash\nexit 0\n")
            fake_codex.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            env["GAME_SCAN_ENV_FILE"] = str(root / "missing.env")
            result = subprocess.run(
                [
                    str(SCRIPTS_DIR / "run_daily_once.sh"),
                    "--date",
                    "2099-01-03",
                    "--dir",
                    str(root / "workspace"),
                ],
                env=env,
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertFalse((root / "workspace/.run-stamps/2099-01-03.done").exists())


class StateCommitTests(unittest.TestCase):
    def test_state_is_updated_from_validated_report_without_duplicate_urls(self):
        """Catches dedup state not advancing, or the same video being appended twice."""
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            (root / "seen_games.json").write_text(
                json.dumps(
                    {
                        "last_updated": "2026-07-24",
                        "games": {
                            "Known Game": {
                                "first_seen": "2026-07-20",
                                "last_seen": "2026-07-24",
                                "seen_videos": [
                                    "https://www.youtube.com/watch?v=old1"
                                ],
                                "developer": "Known Dev",
                                "status": "Launched",
                                "tags": [],
                            }
                        },
                    }
                )
            )
            (root / "2026-07-25.md").write_text(
                """# Game Scout — 2026-07-25

## New Games

### 10:00 upload | New Game — mobile 4X
- [New gameplay](https://www.youtube.com/watch?v=new1) — Channel A · 12:00

**Status:** CBT
**Developer:** New Dev

## New Videos for Known Games

### Known Game — new video
- [Existing](https://www.youtube.com/watch?v=old1) — Channel B · 11:00
- [Fresh](https://www.youtube.com/watch?v=fresh1) — Channel B · 13:00
"""
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "commit_state.py"),
                    "--date",
                    "2026-07-25",
                    "--dir",
                    str(root),
                ],
                text=True,
                capture_output=True,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            state = json.loads((root / "seen_games.json").read_text())
            self.assertEqual(
                [
                    "https://www.youtube.com/watch?v=old1",
                    "https://www.youtube.com/watch?v=fresh1",
                ],
                state["games"]["Known Game"]["seen_videos"],
            )
            self.assertEqual("2026-07-25", state["games"]["Known Game"]["last_seen"])
            self.assertEqual("New Dev", state["games"]["New Game"]["developer"])


if __name__ == "__main__":
    unittest.main()
