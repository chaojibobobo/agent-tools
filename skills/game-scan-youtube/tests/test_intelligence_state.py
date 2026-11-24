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

    suffix = "".join(c for c in name.lower() if c.isascii() and c.isalnum()) or "sample"
    app_id = f"com.fixture.{suffix}"
    listing = f"https://play.google.com/store/apps/details?id={app_id}&gl={country}"
    return {
        "canonical_name": name,
        "aliases": [],
        "package_ids": [app_id],
        "store_urls": [f"https://play.google.com/store/apps/details?id={app_id}"],
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
                "store_url": listing,
                "region_verified": True,
                "region_source_url": listing,
            }
        ],
    }


def ledger(candidates, schema_version=1):
    return {
        "schema_version": schema_version,
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
    def test_store_only_lead_cannot_omit_observations_and_use_a_repost(self):
        lead = candidate(classification="Product Lead", country="US")
        lead["store_urls"] = ["https://example.com/us/launch-rumor"]
        lead["store_observations"] = []
        with self.assertRaisesRegex(ValueError, "Product Lead"):
            self.intel.validate_candidate_ledger(ledger([lead]))

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

    def test_outside_focus_official_listing_is_eligible_not_publishable_by_itself(self):
        lead = candidate(classification="Product Lead", country="US")
        self.intel.validate_candidate_ledger(ledger([lead]))
        with self.assertRaisesRegex(ValueError, "Product Lead"):
            self.intel.validate_report_candidates([{"name":lead["canonical_name"]}], ledger([lead]))
        for source in ("https://apps.apple.com/jp/app/our-game/id12345",
                       "https://itunes.apple.com/lookup?id=12345&country=JP"):
            with self.subTest(source=source):
                lead["store_observations"][0].update(
                    country="JP", store_url="https://apps.apple.com/jp/app/our-game/id12345",
                    region_source_url=source)
                self.intel.validate_candidate_ledger(ledger([lead]))

    def test_store_observation_demands_original_localized_listing_of_same_product(self):
        lead = candidate(classification="Product Lead", country="US")
        row = lead["store_observations"][0]
        for url in ("https://example.org/list?gl=US",
                    "https://play.google.com.example.org/store/apps/details?id=com.fixture.signalgame&gl=US",
                    "https://play.google.com/store/search?q=Signal&gl=US",
                    "https://itunes.apple.com/search?term=Signal&country=US",
                    "https://play.google.com/store/apps/details?id=com.other&gl=US",
                    "https://play.google.com/store/apps/details?id=com.fixture.signalgame&gl=CA&gl=US",
                    "https://play.google.com/store/apps/details?id=com.fixture.signalgame&gl=CA",
                    "https://itunes.apple.com/lookup?id=123,456&country=US"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                row["region_source_url"] = url
                self.intel.validate_candidate_ledger(ledger([lead]))
        focus_lead = candidate(country="MY")
        focus_lead["store_observations"][0]["region_source_url"] = "https://not-a-store.example/items?gl=MY"
        with self.assertRaises(ValueError):
            self.intel.validate_candidate_ledger(ledger([focus_lead]))
        lead["store_observations"][0].update(
            store_url="https://play.google.com/store/apps/details?id=com.wrong.app",
            region_source_url="https://play.google.com/store/apps/details?id=com.wrong.app&gl=US")
        with self.assertRaises(ValueError):
            self.intel.validate_candidate_ledger(ledger([lead]))
        reassigned = candidate(country="MY")
        row = reassigned["store_observations"][0]
        row["store_url"] = row["region_source_url"] = "https://play.google.com/store/apps/details?id=elsewhere&gl=MY"
        with self.assertRaises(ValueError):
            self.intel.validate_candidate_ledger(ledger([reassigned]))
        with self.assertRaises(ValueError):
            self.intel.validate_candidate_ledger(ledger([candidate(country="ZZ")]))

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

    def test_apple_only_country_cannot_be_marked_checked(self):
        data = ledger([candidate()])
        data["product_radar_run"] = {
            "status": "complete",
            "countries": [
                {
                    "code": code,
                    "status": "checked",
                    "source_urls": [
                        f"https://itunes.apple.com/search?term=4X%20strategy&country={code}&entity=software&limit=50"
                    ],
                }
                for code in ("MY", "ID", "PH", "GB", "TH", "CA")
            ],
        }

        with self.assertRaisesRegex(ValueError, "localized Google Play"):
            self.intel.validate_product_radar_run(data)

    def test_changed_focus_keeps_retired_market_history_and_incidental_market_is_not_a_scan(self):
        def run(markets, ended_at):
            return {"focus_markets": markets, "status": "complete", "ended_at": ended_at,
                    "countries": [{"code": c, "status": "checked", "source_urls": [
                        f"https://play.google.com/store/search?gl={c}"]} for c in markets]}

        data = ledger([candidate("Side Listing", "Product Lead", country="KR")])
        data["product_radar_run"] = run(["GB", "JP"], "2026-08-11T03:00:00Z")
        data2 = deepcopy(data)
        data2["product_radar_run"] = run(["JP", "US"], "2026-08-12T03:00:00Z")
        with self.assertRaises(ValueError):
            self.intel.validate_product_radar_run(data, expected_markets=["GB", "JP", "PH"])
        extra = deepcopy(data)
        extra["product_radar_run"]["countries"].append({"code":"KR","status":"not_run","source_urls":[]})
        with self.assertRaises(ValueError):
            self.intel.validate_product_radar_run(extra)
        state = self.intel.update_product_radar({}, data, "2026-08-11", expected_markets=["GB","JP"])
        state = self.intel.update_product_radar(state, data2, "2026-08-12", expected_markets=["JP","US"])
        state = self.intel.update_product_radar(state, data2, "2026-08-12", expected_markets=["JP","US"])
        markets = {item["code"]:item for item in state["countries"]}
        self.assertEqual({"US","JP"}, {c for c,r in markets.items() if r["enabled"]})
        self.assertEqual(1, markets["GB"]["metrics"]["runs_checked"])
        self.assertEqual(2, markets["JP"]["metrics"]["runs_checked"])
        self.assertEqual(1, markets["KR"]["metrics"]["observations"])
        self.assertEqual("Japan", markets["JP"]["name"])
        self.assertEqual(0, markets["KR"]["metrics"]["runs_checked"])
        self.assertIsNone(markets["KR"]["metrics"]["last_checked_at"])
        self.assertEqual(["GB", "JP"], state["run_history"][0]["focus_markets"])
        self.assertEqual(["JP", "US"], state["run_history"][1]["focus_markets"])

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

    def test_v2_reject_cannot_use_early_test_absence_as_counterevidence(self):
        early_reject = candidate("Tutorial Refuge", "Reject")
        early_reject["evidence_stage"] = {
            "progression_depth": "tutorial",
            "world_map_unlock_status": "not_shown",
            "test_stage_uncertainty": True,
            "counterevidence": [],
        }

        with self.assertRaisesRegex(ValueError, "test-stage uncertainty"):
            self.intel.validate_candidate_ledger(
                ledger([early_reject], schema_version=2)
            )

    def test_v2_early_test_candidate_with_missing_world_map_can_remain_pending(self):
        early_pending = candidate("Tutorial Refuge", "Pending")
        early_pending["evidence_stage"] = {
            "progression_depth": "early",
            "world_map_unlock_status": "not_shown",
            "test_stage_uncertainty": True,
            "counterevidence": [],
        }

        self.intel.validate_candidate_ledger(
            ledger([early_pending], schema_version=2)
        )

    def test_v2_reject_accepts_explicit_primary_loop_counterevidence(self):
        action_reject = candidate("Arena Survivor", "Reject")
        action_reject["evidence_stage"] = {
            "progression_depth": "mid",
            "world_map_unlock_status": "absent",
            "test_stage_uncertainty": False,
            "counterevidence": [
                {
                    "source_url": "https://www.youtube.com/watch?v=source1",
                    "detail": "Mid-game footage shows repeated match-based arena runs as the primary loop.",
                }
            ],
        }

        self.intel.validate_candidate_ledger(
            ledger([action_reject], schema_version=2)
        )

    def test_v1_legacy_ledger_remains_valid(self):
        self.intel.validate_candidate_ledger(ledger([candidate("Legacy Reject", "Reject")]))

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
        other = self.three_event_ledger(("US", "KR", "JP"))
        other["product_radar_run"] = {"focus_markets": ["TH","KR"]}
        second = self.intel.update_channel_intelligence(
            channel_state(),
            other,
            "2026-08-11",
        )

        first_channel = first["discovered_channels"][0]
        second_channel = second["discovered_channels"][0]
        self.assertEqual(first_channel["intelligence_score"], second_channel["intelligence_score"])
        self.assertEqual(first_channel["weight"], second_channel["weight"])
        self.assertEqual(first_channel, second_channel)
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

    def test_schema_v2_history_preserves_test_stage_uncertainty(self):
        pending = candidate("Early Shelter", "Pending")
        pending["evidence_stage"] = {
            "progression_depth": "early",
            "world_map_unlock_status": "not_shown",
            "test_stage_uncertainty": True,
            "counterevidence": [],
        }

        state = self.intel.update_candidate_history(
            {"candidates": {}},
            ledger([pending], schema_version=2),
            "2026-08-12",
        )

        item = state["candidates"]["pkg:com.fixture.earlyshelter"]
        self.assertTrue(item["evidence_stage"]["test_stage_uncertainty"])
        self.assertEqual("not_shown", item["evidence_stage"]["world_map_unlock_status"])

    def test_same_day_rerun_corrects_channel_event_classification(self):
        rejected = candidate("Rerun Signal", "Reject")
        rejected["evidence_stage"] = {
            "progression_depth": "early",
            "world_map_unlock_status": "absent",
            "test_stage_uncertainty": False,
            "counterevidence": [
                {
                    "source_url": rejected["videos"][0]["url"],
                    "detail": "Initial review interpreted the early loop as a confirmed genre mismatch.",
                }
            ],
        }
        first = self.intel.update_channel_intelligence(
            channel_state(), ledger([rejected], schema_version=2), "2026-08-12"
        )

        pending = candidate("Rerun Signal", "Pending")
        pending["evidence_stage"] = {
            "progression_depth": "early",
            "world_map_unlock_status": "not_shown",
            "test_stage_uncertainty": True,
            "counterevidence": [],
        }
        corrected = self.intel.update_channel_intelligence(
            first,
            ledger([pending], schema_version=2),
            "2026-08-12",
            replace_candidate_day=True,
        )
        events = corrected["discovered_channels"][0]["intelligence_events"]

        self.assertEqual(1, len(events))
        self.assertEqual("Pending", events[0]["classification"])


if __name__ == "__main__":
    unittest.main()
