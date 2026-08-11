import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"


def core_candidate(name="Last King", video_id="new-video"):
    source_url = f"https://www.youtube.com/watch?v={video_id}"
    present = lambda detail: {
        "status": "present",
        "evidence": [{"source_url": source_url, "detail": detail}],
    }
    return {
        "canonical_name": name,
        "aliases": ["Last Throne"],
        "package_ids": ["com.fixture.lastking"],
        "store_urls": [
            "https://play.google.com/store/apps/details?id=com.fixture.lastking"
        ],
        "classification": "Core",
        "pillars": {
            "base_city": present("persistent city construction"),
            "world_map_territory": present("persistent territory map"),
            "resource_economy": present("resource production and gathering"),
            "army_alliance_war": {"status": "unknown", "evidence": []},
        },
        "missing_pillars": [],
        "recheck_condition": "",
        "videos": [
            {
                "url": source_url,
                "channel": {
                    "channel_id": "UCsignal",
                    "name": "Signal Channel",
                },
                "published": "2026-08-11T01:00:00Z",
                "time_precision": "exact",
                "evidence_quality": 5,
            }
        ],
        "store_observations": [
            {
                "country": "MY",
                "availability": "available",
                "observed_at": "2026-08-11T01:30:00Z",
                "store_url": "https://play.google.com/store/apps/details?id=com.fixture.lastking&gl=MY",
                "region_verified": True,
                "region_source_url": "https://play.google.com/store/apps/details?id=com.fixture.lastking&gl=MY",
            }
        ],
    }


def reject_candidate():
    source_url = "https://www.youtube.com/watch?v=reject-video"
    return {
        "canonical_name": "Arena Defense",
        "aliases": [],
        "package_ids": ["com.fixture.arenadefense"],
        "store_urls": [],
        "classification": "Reject",
        "pillars": {
            "base_city": {
                "status": "present",
                "evidence": [{"source_url": source_url, "detail": "decorative base"}],
            },
            "world_map_territory": {"status": "missing", "evidence": []},
            "resource_economy": {"status": "unknown", "evidence": []},
            "army_alliance_war": {"status": "unknown", "evidence": []},
        },
        "missing_pillars": ["world_map_territory"],
        "recheck_condition": "new persistent world-map gameplay",
        "videos": [
            {
                "url": source_url,
                "channel": {
                    "channel_id": "UCsignal",
                    "name": "Signal Channel",
                },
                "published": "2026-08-11T00:30:00Z",
                "time_precision": "exact",
                "evidence_quality": 3,
            }
        ],
        "store_observations": [],
    }


def ledger(candidates=None):
    return {
        "schema_version": 1,
        "scan_profile": "4X-SLG-STRICT-v1",
        "generated_at": "2026-08-11T02:00:00Z",
        "candidates": candidates or [core_candidate(), reject_candidate()],
        "product_radar_run": {
            "status": "not_run",
            "countries": [
                {"code": code, "status": "not_run", "source_urls": []}
                for code in ("MY", "ID", "PH", "GB", "TH", "CA")
            ],
        },
    }


REPORT = """# Game Scout — 2026-08-11

> **Scan Profile:** 4X-SLG-STRICT-v1

## New Videos for Known Games

### Last Throne — new video
- [Last Throne gameplay](https://www.youtube.com/watch?v=new-video) — Signal Channel · 12:00

**4X Fit:** Core — Base / City + World Map / Territory + Resource Economy
**4X Evidence:** Base/City=persistent city construction; World Map/Territory=persistent territory map; Resource Economy=resource production; Army/Alliance War=unknown
**Package ID:** com.fixture.lastking
**Aliases:** Last King, Last Throne
**Status:** Soft Launch
**Developer:** Fixture Dev
"""


def write_workspace(root: Path, include_ledger=True):
    (root / "2026-08-11.md").write_text(REPORT)
    (root / "channels.json").write_text(
        json.dumps(
            {
                "last_updated": "2026-08-10",
                "seed_channels": [],
                "discovered_channels": [
                    {
                        "id": "@signal",
                        "channel_id": "UCsignal",
                        "name": "Signal Channel",
                        "weight": 3,
                    }
                ],
            }
        )
    )
    (root / "seen_games.json").write_text(
        json.dumps(
            {
                "last_updated": "2026-08-10",
                "games": {
                    "Last King": {
                        "canonical_name": "Last King",
                        "aliases": [],
                        "package_ids": ["com.fixture.lastking"],
                        "first_seen": "2026-08-06",
                        "last_seen": "2026-08-10",
                        "seen_videos": [
                            "https://www.youtube.com/watch?v=old-video"
                        ],
                    }
                },
            }
        )
    )
    (root / "candidate_history.json").write_text(
        json.dumps({"schema_version": 1, "candidates": {}})
    )
    (root / "product_radar.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "last_updated": None,
                "countries": [
                    {
                        "code": code,
                        "name": name,
                        "enabled": True,
                        "metrics": {
                            "last_checked_at": None,
                            "observations": 0,
                            "new_listings": 0,
                            "product_leads": 0,
                            "core_pending_conversions": 0,
                            "youtube_reverse_lookup_successes": 0,
                        },
                    }
                    for code, name in (
                        ("MY", "Malaysia"),
                        ("ID", "Indonesia"),
                        ("PH", "Philippines"),
                        ("GB", "United Kingdom"),
                        ("TH", "Thailand"),
                        ("CA", "Canada"),
                    )
                ],
                "products": {},
            }
        )
    )
    (root / "scan_state.json").write_text(
        json.dumps({"schema_version": 1, "last_successful_scan_end": "2026-08-10T00:00:00Z"})
    )
    (root / "scan-input-2026-08-11.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "window": {
                    "start": "2026-08-10T00:00:00Z",
                    "end": "2026-08-11T02:00:00Z",
                    "hours": 26,
                    "backfill_limited": False,
                },
                "source_summary": {"status": "degraded"},
                "channel_results": [
                    {
                        "channel_id": "UCsignal",
                        "status": "rss_ok",
                        "rss_status": "ok",
                        "page_status": "not_needed",
                    }
                ],
                "videos": [],
            }
        )
    )
    if include_ledger:
        (root / "candidate-ledger-2026-08-11.json").write_text(json.dumps(ledger()))


class IntelligenceValidationTests(unittest.TestCase):
    def test_required_intelligence_ledger_is_a_completion_gate(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            write_workspace(root, include_ledger=False)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "validate_run.py"),
                    "--date",
                    "2026-08-11",
                    "--dir",
                    str(root),
                    "--require-intelligence-ledger",
                ],
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("candidate ledger", result.stderr)

    def test_valid_report_ledger_and_scan_input_pass_together(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            write_workspace(root)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "validate_run.py"),
                    "--date",
                    "2026-08-11",
                    "--dir",
                    str(root),
                    "--require-intelligence-ledger",
                ],
                text=True,
                capture_output=True,
            )

            self.assertEqual(0, result.returncode, result.stderr)


class IntelligenceCommitTests(unittest.TestCase):
    def test_commit_merges_alias_records_reject_and_advances_cursor(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            write_workspace(root)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "commit_state.py"),
                    "--date",
                    "2026-08-11",
                    "--dir",
                    str(root),
                ],
                text=True,
                capture_output=True,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            seen = json.loads((root / "seen_games.json").read_text())
            self.assertIn("Last King", seen["games"])
            self.assertNotIn("Last Throne", seen["games"])
            self.assertIn("Last Throne", seen["games"]["Last King"]["aliases"])
            self.assertIn(
                "https://www.youtube.com/watch?v=new-video",
                seen["games"]["Last King"]["seen_videos"],
            )

            history = json.loads((root / "candidate_history.json").read_text())
            self.assertEqual(
                "Reject",
                history["candidates"]["pkg:com.fixture.arenadefense"]["classification"],
            )
            scan_state = json.loads((root / "scan_state.json").read_text())
            self.assertEqual(
                "2026-08-11T02:00:00Z",
                scan_state["last_successful_scan_end"],
            )
            channels = json.loads((root / "channels.json").read_text())
            channel = channels["discovered_channels"][0]
            self.assertIn("intelligence_score", channel)
            self.assertEqual("healthy", channel["source_health"]["status"])


class ReportMetadataParsingTests(unittest.TestCase):
    def test_parser_keeps_evidence_package_and_alias_fields(self):
        path = SCRIPTS_DIR / "push_feishu.py"
        spec = importlib.util.spec_from_file_location("push_feishu_metadata", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as raw_dir:
            report = Path(raw_dir) / "2026-08-11.md"
            report.write_text(REPORT)
            game = module.parse_report(report)["games"][0]

        self.assertIn("World Map/Territory", game["four_x_evidence"])
        self.assertEqual("com.fixture.lastking", game["package_id"])
        self.assertEqual(["Last King", "Last Throne"], game["aliases"])


if __name__ == "__main__":
    unittest.main()
