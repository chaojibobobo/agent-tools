#!/usr/bin/env python3
"""Prepare a dated early-launch search manifest for the focus markets."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from market_scope import DEFAULT_MARKETS, configured_markets, market_name, validate_markets


def build_query_manifest(report_date: str, focus_markets=DEFAULT_MARKETS) -> dict:
    focus_markets = validate_markets(focus_markets)
    countries = []
    for code in focus_markets:
        name = market_name(code)
        google_terms = [quote("4X strategy"), quote("SLG strategy"), quote("4X early access")]
        apple_terms = [quote("4X strategy"), quote("SLG strategy")]
        google_play_endpoints = [
            f"https://play.google.com/store/search?q={term}&c=apps&hl=en&gl={code}"
            for term in google_terms
        ]
        apple_endpoints = [
            f"https://itunes.apple.com/search?term={term}&country={code}&entity=software&limit=50"
            for term in apple_terms
        ]
        countries.append(
            {
                "code": code,
                "name": name,
                "priority": "required",
                "primary_source": {
                    "store": "google_play",
                    "source_endpoints": google_play_endpoints,
                },
                "secondary_sources": [
                    {
                        "store": "apple_app_store",
                        "role": "release_date_and_ios_availability_cross_check",
                        "source_endpoints": apple_endpoints,
                    }
                ],
                # Backward-compatible flattened view. Ordering is intentional:
                # Google Play must be completed before Apple cross-checks begin.
                "source_endpoints": [*google_play_endpoints, *apple_endpoints],
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
        "focus_markets": list(focus_markets),
        "countries": countries,
        "rules": {
            "primary_store": "google_play",
            "apple_is_secondary_cross_check": True,
            "checked_requires_google_play_localized_source": True,
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
    parser.add_argument("--markets", metavar="MY,JP", help=(
        "Replace the workspace focus markets for this run (comma-separated ISO codes). "
        "Otherwise reads radar-markets.json beside --output, or uses the six defaults."))
    args = parser.parse_args()

    output = Path(args.output).expanduser()
    try:
        markets = (configured_markets(output.parent) if args.markets is None
                   else [code.strip().upper() for code in args.markets.split(",")])
        manifest = build_query_manifest(args.date, markets)
    except ValueError as error:
        parser.error(str(error))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(output)
    print(f"Product radar manifest: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
