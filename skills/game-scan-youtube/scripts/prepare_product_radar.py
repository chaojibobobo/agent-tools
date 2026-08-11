#!/usr/bin/env python3
"""Prepare an inspectable six-country early-launch search manifest."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from intelligence_state import TARGET_COUNTRIES


def build_query_manifest(report_date: str) -> dict:
    countries = []
    for country in TARGET_COUNTRIES:
        name = country["name"]
        code = country["code"]
        encoded_terms = [quote("4X strategy"), quote("SLG strategy")]
        source_endpoints = [
            *[
                f"https://play.google.com/store/search?q={term}&c=apps&hl=en&gl={code}"
                for term in encoded_terms
            ],
            *[
                f"https://itunes.apple.com/search?term={term}&country={code}&entity=software&limit=50"
                for term in encoded_terms
            ],
        ]
        countries.append(
            {
                "code": code,
                "name": name,
                "priority": "required",
                "source_endpoints": source_endpoints,
                "queries": [
                    f'site:play.google.com/store/apps/details "{name}" (4X OR SLG) ("Early Access" OR "Pre-register" OR "Soft Launch")',
                    f'site:apps.apple.com/{code.lower()} "{name}" (4X OR SLG) (strategy OR "soft launch")',
                ],
                "result_contract": {
                    "required": [
                        "canonical_name",
                        "package_id_or_store_id",
                        "developer",
                        "availability",
                        "store_url",
                        "observed_at",
                        "region_verified",
                    ],
                    "availability_values": [
                        "pre_registration",
                        "available",
                        "removed",
                        "unknown",
                    ],
                },
            }
        )
    return {
        "schema_version": 1,
        "report_date": report_date,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "purpose": "independent_product_radar",
        "countries": countries,
        "rules": {
            "does_not_affect_channel_weight": True,
            "region_claim_requires_localized_source_endpoint": True,
            "store_only_results_are_product_leads": True,
            "publication_requires_youtube_and_strict_4x_gate": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(build_query_manifest(args.date), ensure_ascii=False, indent=2) + "\n"
    )
    temporary.replace(output)
    print(f"Product radar manifest: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
