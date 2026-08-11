#!/usr/bin/env python3
"""Commit dedup state only after a report has passed structural validation."""

import argparse
import importlib.util
import json
import re
import sys
from copy import deepcopy
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import intelligence_state


def load_push_module():
    path = SCRIPT_DIR / "push_feishu.py"
    spec = importlib.util.spec_from_file_location("game_scan_push_feishu", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def _normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _candidate_for_report_game(report_game: dict, ledger: dict) -> dict:
    target = _normalized_name(report_game.get("name", ""))
    for candidate in ledger.get("candidates", []):
        names = [candidate.get("canonical_name", ""), *candidate.get("aliases", [])]
        if target in {_normalized_name(name) for name in names if name}:
            return candidate
    raise ValueError(f"report game missing from candidate ledger: {report_game.get('name')}")


def _existing_game_key(games: dict, candidate: dict) -> str:
    canonical_name = candidate.get("canonical_name") or "Unknown"
    if canonical_name in games:
        return canonical_name
    package_ids = set(candidate.get("package_ids", []))
    candidate_names = {
        _normalized_name(name)
        for name in [canonical_name, *candidate.get("aliases", [])]
        if name
    }
    for key, value in games.items():
        if package_ids.intersection(value.get("package_ids", [])):
            return key
        existing_names = {
            _normalized_name(name)
            for name in [key, value.get("canonical_name", ""), *value.get("aliases", [])]
            if name
        }
        if candidate_names.intersection(existing_names):
            return key
    return canonical_name


def _commit_legacy(date: str, work_dir: Path, parsed: dict) -> dict:
    state_path = work_dir / "seen_games.json"
    state = json.loads(state_path.read_text())
    games = state.setdefault("games", {})
    new_games = 0
    updated_games = 0
    new_videos = 0
    for report_game in parsed["games"]:
        urls = [video["url"] for video in report_game.get("videos", []) if video.get("url")]
        if not urls:
            continue
        name = report_game["name"]
        existing = games.get(name)
        if existing is None:
            unique_urls = list(dict.fromkeys(urls))
            games[name] = {
                "first_seen": date,
                "last_seen": date,
                "seen_videos": unique_urls,
                "developer": report_game.get("developer") or "Unknown",
                "status": report_game.get("status") or "New",
                "tags": [],
            }
            new_games += 1
            new_videos += len(unique_urls)
            continue
        seen_urls = existing.setdefault("seen_videos", [])
        additions = [url for url in urls if url not in seen_urls]
        if additions:
            seen_urls.extend(additions)
            existing["last_seen"] = date
            updated_games += 1
            new_videos += len(additions)
    state["last_updated"] = date
    _write_json_atomic(state_path, state)
    return {
        "date": date,
        "new_games": new_games,
        "updated_games": updated_games,
        "new_videos": new_videos,
        "intelligence_ledger": False,
    }


def commit(date: str, work_dir: Path) -> dict:
    report_path = work_dir / f"{date}.md"
    push_module = load_push_module()
    parsed = push_module.parse_report(report_path)
    push_module.validate_4x_profile(
        parsed,
        require_strict=date >= "2026-08-06",
    )
    ledger_path = work_dir / f"candidate-ledger-{date}.json"
    if not ledger_path.exists():
        return _commit_legacy(date, work_dir, parsed)

    ledger = json.loads(ledger_path.read_text())
    intelligence_state.validate_candidate_ledger(ledger)
    intelligence_state.validate_product_radar_run(ledger)
    intelligence_state.validate_report_candidates(parsed["games"], ledger)

    seen_path = work_dir / "seen_games.json"
    channels_path = work_dir / "channels.json"
    history_path = work_dir / "candidate_history.json"
    product_path = work_dir / "product_radar.json"
    scan_state_path = work_dir / "scan_state.json"
    scan_input_path = work_dir / f"scan-input-{date}.json"

    seen_state = json.loads(seen_path.read_text())
    channels_state = json.loads(channels_path.read_text())
    history_state = json.loads(history_path.read_text())
    product_state = json.loads(product_path.read_text())
    scan_state = json.loads(scan_state_path.read_text())
    scan_input = json.loads(scan_input_path.read_text())
    if (scan_input.get("source_summary") or {}).get("status") not in {"complete", "degraded"}:
        raise ValueError("cannot commit a blocked or invalid scan input")

    games = seen_state.setdefault("games", {})
    new_games = 0
    updated_games = 0
    new_videos = 0

    for report_game in parsed["games"]:
        urls = [video["url"] for video in report_game.get("videos", []) if video.get("url")]
        if not urls:
            continue

        candidate = _candidate_for_report_game(report_game, ledger)
        game_key = _existing_game_key(games, candidate)
        existing = games.get(game_key)
        if existing is None:
            existing = {
                "first_seen": date,
                "last_seen": date,
                "seen_videos": [],
                "developer": report_game.get("developer") or "Unknown",
                "status": report_game.get("status") or "New",
                "tags": [],
            }
            games[game_key] = existing
            new_games += 1
        seen_urls = existing.setdefault("seen_videos", [])
        additions = [url for url in urls if url not in seen_urls]
        if additions:
            seen_urls.extend(additions)
            existing["last_seen"] = date
            if existing.get("first_seen") != date:
                updated_games += 1
            new_videos += len(additions)
        existing["canonical_name"] = candidate.get("canonical_name") or game_key
        existing["aliases"] = list(
            dict.fromkeys([*existing.get("aliases", []), *candidate.get("aliases", [])])
        )
        existing["package_ids"] = list(
            dict.fromkeys([*existing.get("package_ids", []), *candidate.get("package_ids", [])])
        )
        existing["store_urls"] = list(
            dict.fromkeys([*existing.get("store_urls", []), *candidate.get("store_urls", [])])
        )
        existing["developer"] = report_game.get("developer") or existing.get("developer", "Unknown")
        existing["status"] = report_game.get("status") or existing.get("status", "New")

    seen_state["last_updated"] = date
    history_state = intelligence_state.update_candidate_history(history_state, ledger, date)
    product_state = intelligence_state.update_product_radar(product_state, ledger, date)
    channels_state = intelligence_state.update_channel_intelligence(channels_state, ledger, date)
    channels_state = intelligence_state.apply_source_health(channels_state, scan_input, date)
    scan_state = deepcopy(scan_state)
    scan_state.setdefault("schema_version", 1)
    scan_state["last_successful_scan_start"] = scan_input["window"]["start"]
    scan_state["last_successful_scan_end"] = scan_input["window"]["end"]
    scan_state["last_successful_report_date"] = date
    scan_state["last_source_status"] = scan_input["source_summary"]["status"]

    for path, value in (
        (seen_path, seen_state),
        (channels_path, channels_state),
        (history_path, history_state),
        (product_path, product_state),
        (scan_state_path, scan_state),
    ):
        _write_json_atomic(path, value)
    return {
        "date": date,
        "new_games": new_games,
        "updated_games": updated_games,
        "new_videos": new_videos,
        "intelligence_ledger": True,
        "candidates_reviewed": len(ledger.get("candidates", [])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--dir", required=True)
    args = parser.parse_args()

    try:
        result = commit(args.date, Path(args.dir).expanduser())
    except Exception as exc:
        print(f"STATE COMMIT FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
