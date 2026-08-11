import importlib.util
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"


def load_module(name: str):
    path = SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def evidence(status="present", label="visible gameplay"):
    return {
        "status": status,
        "evidence": [] if status != "present" else [
            {
                "source_url": "https://www.youtube.com/watch?v=source1",
                "detail": label,
            }
        ],
    }


def candidate(
    name="Signal Game",
    classification="Core",
    *,
    channel_id="UCsignal",
    video_id="source1",
    country="MY",
    published="2026-08-11T01:00:00Z",
    evidence_quality=4,
):
    pillars = {
        "base_city": evidence(),
        "world_map_territory": evidence(),
        "resource_economy": evidence(),
        "army_alliance_war": evidence("missing"),
    }
    missing = []
    videos = [
        {
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "channel": {"channel_id": channel_id, "name": "Signal Channel"},
            "published": published,
            "time_precision": "exact",
            "evidence_quality": evidence_quality,
        }
    ]
    if classification == "Pending":
        pillars["world_map_territory"] = evidence("unknown")
        missing = ["world_map_territory"]
    if classification == "Reject":
        pillars["world_map_territory"] = evidence("missing")
        missing = ["world_map_territory"]
    if classification == "Product Lead":
        pillars = {
            key: evidence("unknown")
            for key in pillars
        }
        missing = ["base_city", "world_map_territory"]
        videos = []

    return {
        "canonical_name": name,
        "aliases": [],
        "package_ids": [f"com.fixture.{name.lower().replace(' ', '')}"],
        "store_urls": [],
        "classification": classification,
        "pillars": pillars,
        "missing_pillars": missing,
        "recheck_condition": "new gameplay evidence" if classification != "Core" else "",
        "videos": videos,
        "store_observations": [
            {
                "country": country,
                "availability": "available",
                "observed_at": "2026-08-11T02:00:00Z",
                "store_url": f"https://play.google.com/store/apps/details?id=fixture&gl={country}",
                "region_verified": True,
                "region_source_url": f"https://play.google.com/store/apps/details?id=fixture&gl={country}",
            }
        ],
    }


def ledger(candidates):
    return {
        "schema_version": 1,
        "scan_profile": "4X-SLG-STRICT-v1",
        "generated_at": "2026-08-11T02:00:00Z",
        "candidates": candidates,
    }


def channel_state():
    return {
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


class ProductRadarStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.intel = load_module("intelligence_state")
        cls.prepare = load_module("prepare_product_radar")

    def test_default_product_radar_has_exactly_six_independent_target_countries(self):
        state = self.intel.new_product_radar_state()

        self.assertEqual(
            ["MY", "ID", "PH", "GB", "TH", "CA"],
            [country["code"] for country in state["countries"]],
        )
        self.assertNotIn("channel_weight", json.dumps(state))

    def test_unsupported_country_is_rejected(self):
        bad = ledger([candidate(country="US")])

        with self.assertRaisesRegex(ValueError, "unsupported product-radar country"):
            self.intel.validate_candidate_ledger(bad)

    def test_country_observation_requires_localized_store_evidence(self):
        bad_candidate = candidate(country="MY")
        observation = bad_candidate["store_observations"][0]
        observation["region_verified"] = False
        observation["region_source_url"] = "https://play.google.com/store/apps/details?id=fixture"

        with self.assertRaisesRegex(ValueError, "localized region evidence"):
            self.intel.validate_candidate_ledger(ledger([bad_candidate]))

    def test_query_manifest_has_country_specific_work_for_all_six_targets(self):
        manifest = self.prepare.build_query_manifest("2026-08-11")

        self.assertEqual(
            ["MY", "ID", "PH", "GB", "TH", "CA"],
            [item["code"] for item in manifest["countries"]],
        )
        for item in manifest["countries"]:
            self.assertGreaterEqual(len(item["queries"]), 2)
            self.assertTrue(all(item["name"] in query for query in item["queries"]))

    def test_product_radar_run_must_account_for_every_target_country(self):
        data = ledger([candidate()])
        data["product_radar_run"] = {
            "status": "degraded",
            "countries": [
                {"code": "MY", "status": "checked", "source_urls": []}
            ],
        }

        with self.assertRaisesRegex(ValueError, "account for exactly"):
            self.intel.validate_product_radar_run(data)

    def test_product_lead_can_be_stored_without_youtube_but_not_published(self):
        lead = candidate(classification="Product Lead")
        data = ledger([lead])

        self.intel.validate_candidate_ledger(data)
        with self.assertRaisesRegex(ValueError, "Product Lead"):
            self.intel.validate_report_candidates(
                [{"name": lead["canonical_name"], "videos": []}],
                data,
            )

    def test_product_radar_tracks_country_runs_without_double_counting_observations(self):
        lead = candidate(classification="Product Lead")
        data = ledger([lead])
        data["product_radar_run"] = {
            "status": "degraded",
            "ended_at": "2026-08-11T03:00:00Z",
            "countries": [
                {
                    "code": code,
                    "status": "checked" if code == "MY" else "not_run",
                    "source_urls": ["https://play.google.com/store/search?gl=MY"] if code == "MY" else [],
                }
                for code in ("MY", "ID", "PH", "GB", "TH", "CA")
            ],
        }

        state = self.intel.new_product_radar_state()
        once = self.intel.update_product_radar(state, data, "2026-08-11")
        twice = self.intel.update_product_radar(once, data, "2026-08-11")
        my_metrics = twice["countries"][0]["metrics"]

        self.assertEqual(1, my_metrics["observations"])
        self.assertEqual(1, my_metrics["runs_checked"])
        self.assertEqual("checked", my_metrics["last_run_status"])
        self.assertEqual("2026-08-11T03:00:00Z", twice["last_successful_scan_end"])
        self.assertEqual(1, len(twice["run_history"]))


class StructuredGenreGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.intel = load_module("intelligence_state")

    def test_core_requires_base_world_and_one_supporting_pillar(self):
        fake_core = candidate()
        fake_core["pillars"]["world_map_territory"] = evidence("unknown")

        with self.assertRaisesRegex(ValueError, "Core requires"):
            self.intel.validate_candidate_ledger(ledger([fake_core]))

    def test_report_video_must_exist_in_matching_ledger_candidate(self):
        data = ledger([candidate()])
        report_games = [
            {
                "name": "Signal Game",
                "videos": [
                    {"url": "https://www.youtube.com/watch?v=different"}
                ],
            }
        ]

        with self.assertRaisesRegex(ValueError, "not present in candidate ledger"):
            self.intel.validate_report_candidates(report_games, data)


class ChannelIntelligenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.intel = load_module("intelligence_state")

    def three_event_ledger(self, countries=("MY", "ID", "PH")):
        return ledger(
            [
                candidate("Core One", "Core", video_id="core1", country=countries[0]),
                candidate("Pending One", "Pending", video_id="pending1", country=countries[1]),
                candidate("Rejected One", "Reject", video_id="reject1", country=countries[2]),
            ]
        )

    def test_target_country_observations_do_not_change_score_or_weight(self):
        first = self.intel.update_channel_intelligence(
            channel_state(),
            self.three_event_ledger(("MY", "ID", "PH")),
            "2026-08-11",
        )
        second = self.intel.update_channel_intelligence(
            channel_state(),
            self.three_event_ledger(("GB", "TH", "CA")),
            "2026-08-11",
        )

        first_channel = first["discovered_channels"][0]
        second_channel = second["discovered_channels"][0]
        self.assertEqual(first_channel["intelligence_score"], second_channel["intelligence_score"])
        self.assertEqual(first_channel["weight"], second_channel["weight"])
        self.assertNotIn("country", json.dumps(first_channel["intelligence_metrics"]))

    def test_source_health_changes_without_changing_intelligence_score(self):
        ranked = self.intel.update_channel_intelligence(
            channel_state(), self.three_event_ledger(), "2026-08-11"
        )
        before = ranked["discovered_channels"][0]["intelligence_score"]
        collection = {
            "channel_results": [
                {
                    "channel_id": "UCsignal",
                    "status": "error",
                    "rss_status": "error",
                    "page_status": "error",
                }
            ]
        }

        updated = self.intel.apply_source_health(ranked, collection, "2026-08-11")
        channel = updated["discovered_channels"][0]
        self.assertEqual(before, channel["intelligence_score"])
        self.assertEqual("unavailable", channel["source_health"]["status"])

    def test_package_identity_and_reject_history_are_persistent(self):
        rejected = candidate("Last King", "Reject")
        rejected["aliases"] = ["Last Throne"]
        rejected["package_ids"] = ["com.fixture.lastking"]
        data = ledger([rejected])

        self.assertEqual("pkg:com.fixture.lastking", self.intel.candidate_identity(rejected))
        state = self.intel.update_candidate_history({"candidates": {}}, data, "2026-08-11")
        item = state["candidates"]["pkg:com.fixture.lastking"]
        self.assertEqual("Reject", item["classification"])
        self.assertIn("Last Throne", item["aliases"])


if __name__ == "__main__":
    unittest.main()
