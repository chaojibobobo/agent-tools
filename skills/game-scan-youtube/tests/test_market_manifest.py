"""Offline tests for configured market manifests and replayable daily scope."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from prepare_product_radar import build_query_manifest


class MarketManifestTests(unittest.TestCase):
    day = "2026-11-24"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        self.config = self.directory / "radar-markets.json"
        self.output = self.directory / f"product-radar-input-{self.day}.json"

    def generate(self, *args):
        return subprocess.run([sys.executable, "-B", str(SCRIPTS / "prepare_product_radar.py"),
                               "--date", self.day, "--output", str(self.output), *args],
                              capture_output=True, text=True)

    def test_default_matches_legacy_and_snapshots_six_markets(self):
        manifest = build_query_manifest(self.day)
        self.assertEqual(manifest.get("focus_markets"), ["MY", "ID", "PH", "GB", "TH", "CA"])
        self.assertEqual(manifest["focus_markets"], [item["code"] for item in manifest["countries"]])

    def test_workspace_can_replace_defaults_and_localizes_queries(self):
        self.config.write_text(json.dumps({"focus_markets": ["JP", "MY"]}))
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(self.output.read_text())
        self.assertEqual([c["code"] for c in manifest["countries"]], ["JP", "MY"])
        self.assertEqual(manifest["focus_markets"], ["JP", "MY"])
        japan = manifest["countries"][0]
        self.assertEqual(japan["name"], "Japan")
        self.assertTrue(all("gl=JP" in url for url in japan["primary_source"]["source_endpoints"]))
        self.assertTrue(all("country=JP" in url for url in japan["secondary_sources"][0]["source_endpoints"]))

    def test_cli_overrides_workspace_without_mutating_it(self):
        source = json.dumps({"focus_markets": ["DE"]})
        self.config.write_text(source)
        result = self.generate("--markets", " jp ,br ")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.config.read_text(), source)
        self.assertEqual(json.loads(self.output.read_text())["focus_markets"], ["JP", "BR"])

    def test_bad_config_cannot_overwrite_existing_manifest(self):
        self.output.write_text("existing")
        for source in ("{", "[]", "{}", '{"focus_markets":[]}', '{"focus_markets":"JP"}',
                       '{"focus_markets":["JP","ZZ"]}', '{"focus_markets":["JP","JP"]}'):
            with self.subTest(source=source):
                self.config.write_text(source)
                result = self.generate()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("radar-markets.json", result.stderr)
                self.assertEqual(self.output.read_text(), "existing")

    def test_codes_are_assigned_iso_markets_not_arbitrary_two_letters(self):
        import market_scope
        self.assertTrue(all(market_scope.is_market_code(c) for c in ("JP", "BR", "RE", "CW", "TW")))
        self.assertFalse(any(market_scope.is_market_code(c) for c in ("UK", "ZZ", "AA", "jp", " JP", [], None)))
        self.assertEqual(market_scope.validate_markets(["JP", "BR"]), ("JP", "BR"))
        for values in ([], {}, "JP", ["JP", "JP"], ["US", "ZZ"], [False]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                market_scope.validate_markets(values)

    def test_snapshot_freezes_scope_after_config_changes_with_legacy_compatibility(self):
        from market_scope import expected_run_markets
        self.assertEqual(expected_run_markets(self.directory, self.day), ("MY", "ID", "PH", "GB", "TH", "CA"))
        self.config.write_text(json.dumps({"focus_markets": ["DE"]}))
        self.assertEqual(expected_run_markets(self.directory, self.day), ("DE",))
        manifest = {"report_date": self.day, "countries": [{"code": "JP"}, {"code": "MY"}]}
        for extra in ({}, {"focus_markets": ["JP", "MY"]}):
            self.output.write_text(json.dumps({**manifest, **extra}))
            self.assertEqual(expected_run_markets(self.directory, self.day), ("JP", "MY"))

    def test_rejects_corrupt_or_mismatched_manifest_instead_of_using_config(self):
        from market_scope import expected_run_markets
        manifest = {"report_date": self.day, "countries": [{"code": "JP"}, {"code": "MY"}]}
        for changes in ({"report_date": "2026-11-23"}, {"focus_markets": ["JP"]},
                        {"focus_markets": ["MY", "JP"]}, {"countries": []},
                        {"countries": [{"code": "ZZ"}]}, {"countries": ["JP"]},
                        {"focus_markets": None}, {"countries": [{"code": "JP"}, {"code": "JP"}]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.output.write_text(json.dumps({**manifest, **changes}))
                expected_run_markets(self.directory, self.day)
