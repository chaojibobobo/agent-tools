"""Exercise the public README example against the real, offline manifest CLI."""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MarketDocumentationTests(unittest.TestCase):
    def test_readme_market_configuration_changes_the_manifest(self):
        readme = (ROOT / "README.md").read_text()
        configs = [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", readme, re.S)]
        config = next((item for item in configs if "focus_markets" in item), None)
        self.assertIsNotNone(config, "README needs an actionable radar-markets.json example")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "radar-markets.json").write_text(json.dumps(config))
            output = workspace / "product-radar-input-2026-01-01.json"
            args = [sys.executable, "-B", str(ROOT / "scripts/prepare_product_radar.py"),
                    "--date", "2026-01-01", "--output", str(output)]
            subprocess.run(args, capture_output=True, text=True, check=True)
            self.assertEqual(config["focus_markets"], json.loads(output.read_text())["focus_markets"])
            override = re.search(r"--markets ([A-Z]{2}(?:,[A-Z]{2})*)", readme)
            self.assertIsNotNone(override, "README needs a one-run override example")
            subprocess.run([*args, "--markets", override[1]], capture_output=True, text=True, check=True)
            self.assertEqual(override[1].split(","), json.loads(output.read_text())["focus_markets"])
            self.assertEqual(config, json.loads((workspace / "radar-markets.json").read_text()))
