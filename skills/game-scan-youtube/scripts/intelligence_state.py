#!/usr/bin/env python3
"""Pure state and validation rules for game-scan-youtube intelligence signals."""

import json
import re
import sys
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlparse

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from decision_card import youtube_video_id
from market_scope import DEFAULT_MARKETS, is_market_code, market_name, validate_markets


# Backward-compatible names for the default configuration, not an admission list.
TARGET_COUNTRIES = tuple({"code": c, "name": market_name(c)} for c in DEFAULT_MARKETS)
TARGET_COUNTRY_CODES = DEFAULT_MARKETS
PILLAR_KEYS = (
    "base_city",
    "world_map_territory",
    "resource_economy",
    "army_alliance_war",
)
CLASSIFICATIONS = {"Core", "Pending", "Reject", "Product Lead"}
PROGRESSION_DEPTHS = {"tutorial", "early", "mid", "late", "unknown"}
WORLD_MAP_UNLOCK_STATUSES = {
    "shown",
    "not_shown",
    "known_later",
    "absent",
    "unknown",
}
SCORE_WEIGHTS = {
    "strict_4x_yield": 0.40,
    "early_discovery": 0.25,
    "unique_discovery": 0.20,
    "evidence_quality": 0.15,
}


def _market_record(code: str, enabled: bool) -> dict:
    return {
        "code": code, "name": market_name(code), "enabled": enabled,
        "metrics": {
            "last_checked_at": None, "last_run_status": None,
            "runs_checked": 0, "runs_failed": 0, "runs_not_run": 0,
            "observations": 0, "new_listings": 0, "product_leads": 0,
            "core_pending_conversions": 0, "youtube_reverse_lookup_successes": 0,
        },
    }


def new_product_radar_state() -> dict:
    return {
        "schema_version": 1,
        "last_updated": None,
        "last_successful_scan_end": None,
        "countries": [_market_record(code, True) for code in DEFAULT_MARKETS],
        "products": {},
        "run_history": [],
    }


def _normalized_name(value: str) -> str:
    return "".join(c for c in (value or "").casefold() if c.isalnum())


def _official_url(value):
    """Only direct official storefronts. Reject ambiguous hosts/ports and credentials."""
    try:
        p = urlparse(value)
        if (p.scheme != "https" or p.username is not None or p.password is not None
                or p.port not in (None, 443)
                or p.hostname not in {"play.google.com", "apps.apple.com", "itunes.apple.com"}):
            return None
        return p
    except (AttributeError, TypeError, ValueError):
        return None


def _listing_id(value, country=None):
    """Return a product identity, not a search/category URL or a lookalike query."""
    p = _official_url(value)
    if p is None:
        return None
    query = parse_qs(p.query, keep_blank_values=True)
    app_ids = query.get("id", [])
    if p.hostname == "play.google.com" and p.path == "/store/apps/details":
        if (len(app_ids) == 1 and re.fullmatch(r"[\w.]+", app_ids[0], re.ASCII)
                and (not country or [v.upper() for v in query.get("gl", [])] == [country])):
            return ("google_play", app_ids[0])
    if p.hostname == "apps.apple.com":
        item = re.fullmatch(r"/([a-zA-Z]{2})/app/(?:[^/]+/)?id([0-9]+)/?", p.path)
        if item and (not country or item[1].upper() == country):
            return ("app_store", item[2])
    if p.hostname == "itunes.apple.com" and p.path == "/lookup":
        if (len(app_ids) == 1 and re.fullmatch(r"[0-9]+", app_ids[0])
                and (not country or [v.upper() for v in query.get("country", [])] == [country])):
            return ("app_store", app_ids[0])
    return None


def _is_localized_google_play_url(value: str, country: str) -> bool:
    parsed = _official_url(value)
    return bool(parsed and parsed.hostname == "play.google.com"
                and parsed.path in {"/store/search", "/store/apps/details"}
                and [v.upper() for v in parse_qs(parsed.query, keep_blank_values=True).get("gl", [])] == [country])


def candidate_identity(candidate: dict) -> str:
    package_ids = [item.strip().lower() for item in candidate.get("package_ids", []) if item.strip()]
    if package_ids:
        return f"pkg:{package_ids[0]}"
    store_urls = [item.strip() for item in candidate.get("store_urls", []) if item.strip()]
    if store_urls:
        return f"store:{store_urls[0]}"
    name = _normalized_name(candidate.get("canonical_name", ""))
    if not name:
        raise ValueError("candidate has no stable identity")
    return f"name:{name}"


def _status(pillars: dict, key: str) -> str:
    value = pillars.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"candidate pillar missing: {key}")
    status = value.get("status")
    if status not in {"present", "missing", "unknown"}:
        raise ValueError(f"invalid pillar status for {key}: {status!r}")
    if status == "present" and not value.get("evidence"):
        raise ValueError(f"present pillar has no evidence: {key}")
    return status


def _validate_evidence_stage(candidate: dict, identity: str) -> dict:
    stage = candidate.get("evidence_stage")
    if not isinstance(stage, dict):
        raise ValueError(f"schema v2 candidate has no evidence_stage: {identity}")
    if stage.get("progression_depth") not in PROGRESSION_DEPTHS:
        raise ValueError(f"invalid progression_depth for {identity}")
    if stage.get("world_map_unlock_status") not in WORLD_MAP_UNLOCK_STATUSES:
        raise ValueError(f"invalid world_map_unlock_status for {identity}")
    if not isinstance(stage.get("test_stage_uncertainty"), bool):
        raise ValueError(f"test_stage_uncertainty must be boolean for {identity}")

    counterevidence = stage.get("counterevidence")
    if not isinstance(counterevidence, list):
        raise ValueError(f"counterevidence must be a list for {identity}")
    for item in counterevidence:
        if not isinstance(item, dict):
            raise ValueError(f"counterevidence entry must be an object for {identity}")
        if not re.match(r"https?://", item.get("source_url", "")):
            raise ValueError(f"counterevidence needs a source URL for {identity}")
        if not item.get("detail", "").strip():
            raise ValueError(f"counterevidence needs a concrete detail for {identity}")
    return stage


def validate_candidate_ledger(ledger: dict) -> None:
    schema_version = ledger.get("schema_version")
    if schema_version not in {1, 2}:
        raise ValueError("candidate ledger schema_version must be 1 or 2")
    if ledger.get("scan_profile", "").strip().lower() != "4x-slg-strict-v1":
        raise ValueError("candidate ledger must use 4X-SLG-STRICT-v1")
    candidates = ledger.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("candidate ledger candidates must be a list")

    identities = set()
    for candidate in candidates:
        identity = candidate_identity(candidate)
        if identity in identities:
            raise ValueError(f"duplicate candidate identity: {identity}")
        identities.add(identity)

        classification = candidate.get("classification")
        if classification not in CLASSIFICATIONS:
            raise ValueError(f"invalid classification for {identity}: {classification!r}")
        pillars = candidate.get("pillars") or {}
        statuses = {key: _status(pillars, key) for key in PILLAR_KEYS}
        videos = candidate.get("videos") or []
        evidence_stage = (
            _validate_evidence_stage(candidate, identity)
            if schema_version == 2
            else None
        )

        # The same evidence rules apply everywhere, independently of the focus
        # configuration. Multiple known packages/URLs can represent linked editions.
        declared = {_listing_id(url) for url in candidate.get("store_urls") or []}
        declared.discard(None)
        for pid in candidate.get("package_ids") or []:
            declared.add(("app_store" if pid.isdigit() else "google_play", pid))
        observations = candidate.get("store_observations") or []
        if classification == "Product Lead" and not observations:
            raise ValueError(f"Product Lead needs a first-party regional store observation: {identity}")
        for observation in observations:
            country = observation.get("country")
            if not is_market_code(country):
                raise ValueError(f"unsupported product-radar country: {country!r}")
            if observation.get("availability") not in {
                "pre_registration",
                "available",
                "removed",
                "unknown",
            }:
                raise ValueError(f"invalid store availability for {country}")
            listing = _listing_id(observation.get("region_source_url"), country)
            if (observation.get("region_verified") is not True or not listing
                    or _listing_id(observation.get("store_url")) != listing):
                raise ValueError(
                    f"store observation lacks localized region evidence: {country}"
                )
            if any(platform == listing[0] for platform, _ in declared) and listing not in declared:
                raise ValueError(f"store observation product ID mismatch: {country}")
            if not observation.get("observed_at"):
                raise ValueError(f"store observation is incomplete: {country}")

        for video in videos:
            url = video.get("url", "")
            if not youtube_video_id(url):
                raise ValueError(f"candidate video is not a direct YouTube source: {url!r}")
            quality = video.get("evidence_quality", 3)
            if not isinstance(quality, int) or not 1 <= quality <= 5:
                raise ValueError("video evidence_quality must be an integer from 1 to 5")

        if classification == "Core":
            supporting = (
                statuses["resource_economy"] == "present"
                or statuses["army_alliance_war"] == "present"
            )
            if not (
                statuses["base_city"] == "present"
                and statuses["world_map_territory"] == "present"
                and supporting
            ):
                raise ValueError(
                    f"Core requires Base / City + World Map / Territory + one supporting pillar: {identity}"
                )
            if not videos:
                raise ValueError(f"Core candidate has no YouTube evidence: {identity}")
            if evidence_stage and evidence_stage["world_map_unlock_status"] not in {
                "shown",
                "known_later",
            }:
                raise ValueError(
                    f"Core requires positive world-map unlock evidence: {identity}"
                )
        elif classification == "Pending":
            if not candidate.get("missing_pillars"):
                raise ValueError(f"Pending candidate must name missing pillars: {identity}")
            if not videos:
                raise ValueError(f"Pending candidate has no YouTube evidence: {identity}")
        elif classification == "Reject":
            if not candidate.get("recheck_condition"):
                raise ValueError(f"Reject candidate must name a recheck condition: {identity}")
            if schema_version == 2:
                counterevidence = evidence_stage["counterevidence"]
                if evidence_stage["test_stage_uncertainty"] and not counterevidence:
                    raise ValueError(
                        "test-stage uncertainty cannot be rejected from absence alone: "
                        f"{identity}"
                    )
                if not counterevidence:
                    raise ValueError(
                        f"schema v2 Reject requires explicit counterevidence: {identity}"
                    )


def validate_product_radar_run(ledger: dict, expected_markets=None) -> None:
    run = ledger.get("product_radar_run")
    if not isinstance(run, dict):
        raise ValueError("candidate ledger has no product_radar_run")
    if run.get("status") not in {"complete", "degraded", "not_run"}:
        raise ValueError(f"invalid product-radar status: {run.get('status')!r}")
    countries = run.get("countries")
    if not isinstance(countries, list):
        raise ValueError("product-radar countries must be a list")
    if any(not isinstance(item, dict) for item in countries):
        raise ValueError("each product-radar country must be an object")
    expected = validate_markets(expected_markets if expected_markets is not None
                                else run.get("focus_markets", DEFAULT_MARKETS))
    if "focus_markets" in run and set(validate_markets(run["focus_markets"])) != set(expected):
        raise ValueError("product_radar_run focus_markets differs from the run manifest")
    codes = [item.get("code") for item in countries]
    if (not all(is_market_code(c) for c in codes)
            or set(codes) != set(expected) or len(codes) != len(expected)):
        raise ValueError("product_radar_run must account for exactly " + ", ".join(expected))
    statuses = []
    for item in countries:
        if item.get("status") not in {"checked", "failed", "not_run"}:
            raise ValueError(
                f"invalid country radar status for {item.get('code')}: {item.get('status')!r}"
            )
        if not isinstance(item.get("source_urls", []), list):
            raise ValueError(f"source_urls must be a list for {item.get('code')}")
        if item.get("status") == "checked":
            source_urls = item.get("source_urls") or []
            if not any(
                _is_localized_google_play_url(url, item["code"])
                for url in source_urls
            ):
                raise ValueError(
                    "checked product-radar country has no localized Google Play "
                    f"source URL: {item.get('code')}"
                )
        statuses.append(item.get("status"))
    if run.get("status") == "complete" and any(status != "checked" for status in statuses):
        raise ValueError("complete product-radar run requires every country checked")
    if run.get("status") == "not_run" and any(status != "not_run" for status in statuses):
        raise ValueError("not_run product-radar run requires every country not_run")
    if run.get("status") == "degraded" and all(status == "not_run" for status in statuses):
        raise ValueError("all-not-run product radar must use status not_run")


def _candidate_names(candidate: dict) -> Iterable[str]:
    yield candidate.get("canonical_name", "")
    yield from candidate.get("aliases") or []


def validate_report_candidates(report_games: List[dict], ledger: dict) -> None:
    validate_candidate_ledger(ledger)
    by_name = {}
    for candidate in ledger["candidates"]:
        for name in _candidate_names(candidate):
            normalized = _normalized_name(name)
            if normalized:
                by_name[normalized] = candidate

    for game in report_games:
        candidate = by_name.get(_normalized_name(game.get("name", "")))
        if not candidate:
            raise ValueError(f"published game is missing from candidate ledger: {game.get('name')}")
        if candidate["classification"] in {"Reject", "Product Lead"}:
            raise ValueError(
                f"{candidate['classification']} cannot be published: {game.get('name')}"
            )
        ledger_ids = {youtube_video_id(video["url"]) for video in candidate.get("videos") or []}
        report_ids = {youtube_video_id(video.get("url")) for video in game.get("videos") or []}
        if not report_ids or None in report_ids or not report_ids.issubset(ledger_ids):
            raise ValueError(
                f"report video not present in candidate ledger: {game.get('name')}"
            )


def _all_channels(state: dict) -> Iterable[dict]:
    yield from state.get("seed_channels", [])
    yield from state.get("discovered_channels", [])


def _score_to_weight(score: float, reviewed: int, valid_hits: int) -> int:
    if reviewed < 3:
        return 4 if valid_hits else 2
    if score >= 85:
        return 9
    if score >= 70:
        return 8
    if score >= 55:
        return 7
    if score >= 40:
        return 6
    if score >= 25:
        return 5
    if score >= 15:
        return 4
    return 1 if valid_hits == 0 else 2


def _event_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def _candidate_channel_facts(candidate: dict) -> Dict[str, dict]:
    videos = candidate.get("videos") or []
    channel_ids = {
        (video.get("channel") or {}).get("channel_id")
        for video in videos
        if (video.get("channel") or {}).get("channel_id")
    }
    exact = [
        video
        for video in videos
        if video.get("time_precision") == "exact" and video.get("published")
        and video.get("time_status") != "unverified"
    ]
    earliest = min((video["published"] for video in exact), default=None)
    facts = {}
    for video in videos:
        channel_id = (video.get("channel") or {}).get("channel_id")
        if not channel_id:
            continue
        item = facts.setdefault(
            channel_id,
            {
                "lead": False,
                "unique": len(channel_ids) == 1,
                "evidence_quality": [],
                "urls": [],
            },
        )
        if earliest and video in exact and video.get("published") == earliest:
            item["lead"] = True
        item["evidence_quality"].append(video.get("evidence_quality", 3))
        item["urls"].append(video.get("url", ""))
    return facts


def update_channel_intelligence(
    channels: dict,
    ledger: dict,
    event_date: str,
    *,
    replace_candidate_day: bool = False,
) -> dict:
    """Update channel intelligence without reading store/country observations."""
    validate_candidate_ledger(ledger)
    updated = deepcopy(channels)
    channel_map = {
        channel.get("channel_id"): (group_name, channel)
        for group_name in ("seed_channels", "discovered_channels")
        for channel in updated.get(group_name, [])
        if channel.get("channel_id")
    }

    for candidate in ledger["candidates"]:
        identity = candidate_identity(candidate)
        for channel_id, facts in _candidate_channel_facts(candidate).items():
            if channel_id not in channel_map:
                continue
            _, channel = channel_map[channel_id]
            history = channel.setdefault("intelligence_events", [])
            if replace_candidate_day:
                history[:] = [
                    item
                    for item in history
                    if not (
                        item.get("candidate_id") == identity
                        and item.get("date") == event_date
                    )
                ]
            event_id = f"{identity}|{event_date}|{'|'.join(sorted(facts['urls']))}"
            event = {
                "event_id": event_id,
                "date": event_date,
                "candidate_id": identity,
                "classification": candidate["classification"],
                "lead": facts["lead"],
                "unique": facts["unique"],
                "evidence_quality": round(
                    sum(facts["evidence_quality"]) / len(facts["evidence_quality"]), 2
                ),
            }
            existing_event = next(
                (item for item in history if item.get("event_id") == event_id),
                None,
            )
            if existing_event is None:
                history.append(event)
            else:
                # A same-day deep rerun may correct an evidence-stage judgment.
                # Replace the event facts instead of freezing the first verdict.
                existing_event.update(event)

    event_day = _event_date(event_date)
    ninety_day_cutoff = event_day - timedelta(days=89)
    thirty_day_cutoff = event_day - timedelta(days=29)
    for group_name in ("seed_channels", "discovered_channels"):
        for channel in updated.get(group_name, []):
            history = [
                item
                for item in channel.get("intelligence_events", [])
                if _event_date(item["date"]) >= ninety_day_cutoff
            ]
            channel["intelligence_events"] = history
            recent = [item for item in history if _event_date(item["date"]) >= thirty_day_cutoff]
            reviewed = len(recent)
            core = sum(item["classification"] == "Core" for item in recent)
            pending = sum(item["classification"] == "Pending" for item in recent)
            valid = [item for item in recent if item["classification"] in {"Core", "Pending"}]
            valid_count = len(valid)
            genre_yield = ((core + 0.5 * pending) / reviewed * 100) if reviewed else 0.0
            lead = (sum(bool(item.get("lead")) for item in valid) / valid_count * 100) if valid_count else 0.0
            unique = (sum(bool(item.get("unique")) for item in valid) / valid_count * 100) if valid_count else 0.0
            quality = (
                sum(float(item.get("evidence_quality", 3)) for item in valid)
                / valid_count
                / 5
                * 100
                if valid_count
                else 0.0
            )
            score = round(
                genre_yield * SCORE_WEIGHTS["strict_4x_yield"]
                + lead * SCORE_WEIGHTS["early_discovery"]
                + unique * SCORE_WEIGHTS["unique_discovery"]
                + quality * SCORE_WEIGHTS["evidence_quality"],
                2,
            )
            channel["intelligence_score"] = score
            channel["intelligence_metrics"] = {
                "window_days": 30,
                "reviewed": reviewed,
                "core_hits": core,
                "pending_hits": pending,
                "rejects": sum(item["classification"] == "Reject" for item in recent),
                "strict_4x_yield": round(genre_yield, 2),
                "early_discovery": round(lead, 2),
                "unique_discovery": round(unique, 2),
                "evidence_quality": round(quality, 2),
            }
            if group_name == "seed_channels" and channel.get("weight") == 10:
                channel["weight"] = 10
            elif reviewed:
                channel["weight"] = _score_to_weight(score, reviewed, valid_count)
    updated["last_updated"] = event_date
    return updated


def apply_source_health(channels: dict, collection: dict, event_date: str) -> dict:
    updated = deepcopy(channels)
    result_map = {
        item.get("channel_id"): item
        for item in collection.get("channel_results", [])
        if item.get("channel_id")
    }
    for channel in _all_channels(updated):
        result = result_map.get(channel.get("channel_id"))
        if not result:
            continue
        status = result.get("status")
        if status in {"rss_ok", "ok"}:
            health = "healthy"
        elif status == "page_ok":
            health = "fallback"
        else:
            health = "unavailable"
        channel["source_health"] = {
            "status": health,
            "checked_at": event_date,
            "rss_status": result.get("rss_status"),
            "page_status": result.get("page_status"),
            **({"fetch_tier": result["fetch_tier"]} if result.get("fetch_tier") else {}),
        }
    return updated


def update_candidate_history(state: dict, ledger: dict, event_date: str) -> dict:
    validate_candidate_ledger(ledger)
    updated = deepcopy(state)
    candidates = updated.setdefault("candidates", {})
    for candidate in ledger["candidates"]:
        identity = candidate_identity(candidate)
        existing = candidates.setdefault(identity, {})
        aliases = list(
            dict.fromkeys(
                [*existing.get("aliases", []), *candidate.get("aliases", [])]
            )
        )
        existing.update(
            {
                "canonical_name": candidate.get("canonical_name"),
                "aliases": aliases,
                "package_ids": candidate.get("package_ids", []),
                "store_urls": candidate.get("store_urls", []),
                "classification": candidate.get("classification"),
                "pillars": candidate.get("pillars", {}),
                "evidence_stage": candidate.get("evidence_stage", {}),
                "missing_pillars": candidate.get("missing_pillars", []),
                "recheck_condition": candidate.get("recheck_condition", ""),
                "last_reviewed": event_date,
            }
        )
        existing.setdefault("first_reviewed", event_date)
        pending = dict.fromkeys(existing.get("pending_video_ids", []))
        for video in candidate.get("videos", []):
            vid = youtube_video_id(video.get("url"))
            if not vid:
                continue
            if candidate["classification"] in {"Core", "Pending"} and video.get("time_status") == "unverified":
                pending[vid] = None
            elif video.get("time_status") == "verified" or (
                video.get("time_precision") == "exact" and video.get("published")
            ):
                pending.pop(vid, None)
        if pending or "pending_video_ids" in existing:
            existing["pending_video_ids"] = list(pending)[-32:]
    updated["last_updated"] = event_date
    updated.setdefault("schema_version", 1)
    return updated


def update_product_radar(state: dict, ledger: dict, event_date: str, expected_markets=None) -> dict:
    validate_candidate_ledger(ledger)
    validate_product_radar_run(ledger, expected_markets)
    run = ledger["product_radar_run"]
    focus = validate_markets(expected_markets if expected_markets is not None
                             else run.get("focus_markets", DEFAULT_MARKETS))
    updated = deepcopy(state) if state else new_product_radar_state()
    country_map = {item["code"]: item for item in updated.get("countries", [])}
    observed = [row["country"] for c in ledger["candidates"] for row in c.get("store_observations") or []]
    for code in (*focus, *observed):
        if code not in country_map:
            country_map[code] = _market_record(code, code in focus)
    for country in country_map.values():
        country["enabled"] = country["code"] in focus
        metrics = country.setdefault("metrics", {})
        for key, default in _market_record(country["code"], country["enabled"])["metrics"].items():
            metrics.setdefault(key, default)
    updated["countries"] = list(country_map.values())
    run_signature = "|".join(
        f"{item['code']}:{item['status']}" for item in run["countries"]
    )
    run_id = run.get("run_id") or run.get("ended_at") or f"{event_date}|{run_signature}"
    run_history = updated.setdefault("run_history", [])
    if not any(item.get("run_id") == run_id for item in run_history):
        run_history.append(
            {
                "run_id": run_id,
                "date": event_date,
                "status": run["status"],
                "ended_at": run.get("ended_at") or event_date,
                "focus_markets": list(focus),
                "countries": deepcopy(run["countries"]),
            }
        )
        for run_country in run["countries"]:
            metrics = country_map[run_country["code"]]["metrics"]
            run_status = run_country["status"]
            metrics[f"runs_{run_status}"] += 1
            metrics["last_run_status"] = run_status
            if run_status == "checked":
                metrics["last_checked_at"] = run.get("ended_at") or event_date
    if run["status"] in {"complete", "degraded"}:
        updated["last_successful_scan_end"] = run.get("ended_at") or event_date
    products = updated.setdefault("products", {})
    for candidate in ledger["candidates"]:
        identity = candidate_identity(candidate)
        observations = candidate.get("store_observations") or []
        if not observations:
            continue
        product = products.setdefault(
            identity,
            {
                "canonical_name": candidate.get("canonical_name"),
                "aliases": candidate.get("aliases", []),
                "package_ids": candidate.get("package_ids", []),
                "observations": [],
            },
        )
        existing_observations = {
            json.dumps(item, sort_keys=True) for item in product.get("observations", [])
        }
        for observation in observations:
            key = json.dumps(observation, sort_keys=True)
            if key not in existing_observations:
                product.setdefault("observations", []).append(observation)
                existing_observations.add(key)
                metrics = country_map[observation["country"]]["metrics"]
                metrics["last_observed_at"] = observation.get("observed_at") or event_date
                metrics["observations"] += 1
                if observation.get("availability") in {"available", "pre_registration"}:
                    metrics["new_listings"] += 1
                if candidate["classification"] == "Product Lead":
                    metrics["product_leads"] += 1
                if candidate["classification"] in {"Core", "Pending"}:
                    metrics["core_pending_conversions"] += 1
                if candidate.get("videos"):
                    metrics["youtube_reverse_lookup_successes"] += 1
        product["classification"] = candidate["classification"]
        product["last_seen"] = event_date
    updated["last_updated"] = event_date
    return updated
