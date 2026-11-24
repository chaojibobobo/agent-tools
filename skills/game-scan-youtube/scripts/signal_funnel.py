"""Join report claims to the *same* video and product before rendering or committing."""

from datetime import datetime

import decision_card
from intelligence_state import _normalized_name
from run_health import health_banner, is_degraded


def _exact(value):
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return timestamp.utcoffset() is not None
    except (AttributeError, TypeError, ValueError):
        return False


def _time(video, modern):
    status = video.get("time_status")
    if status not in (None, "verified", "unverified"):
        raise ValueError(f"invalid time_status: {status!r}")
    if status == "unverified" or (modern and (
        video.get("source_kind") == "channel_page" or video.get("time_precision") == "relative"
    )):
        return "unverified"
    if _exact(video.get("published")):
        return "verified"
    return "unverified" if modern or status == "verified" else None


def _names(entry):
    return {_normalized_name(s) for s in [entry.get("canonical_name"), *entry.get("aliases", [])] if s}


def prepare_report(parsed, scan, ledger=None, seen=None):
    """Mutate only the in-memory report. Legacy reports need no new JSON fields."""
    scan = scan or {}
    if is_degraded(scan):
        raise ValueError(f"RUN_DEGRADED {health_banner(scan)}：禁止校验为正常日报")
    modern = scan.get("run_status") is not None or "source_health" in scan
    parsed["card_v3_required"] = modern or parsed.get("card_v3_required", False)
    by_id, prior_window = {}, set()
    for v in scan.get("videos", []):
        key = v.get("video_id") or decision_card.youtube_video_id(v.get("url", ""))
        if key:
            by_id[key] = _time(v, modern)
            if v.get("verification_update"):
                prior_window.add(key)
    candidates = {}
    for candidate in (ledger or {}).get("candidates", []):
        for video in candidate.get("videos", []):
            video_id = decision_card.youtube_video_id(video.get("url", ""))
            if by_id.get(video_id) == "unverified" or _time(video, modern) == "unverified" and not by_id.get(video_id):
                # Keep unreported candidates honest too: channel scoring and
                # tomorrow's re-check queue consume this same in-memory ledger.
                video["time_status"] = "unverified"
        for name in _names(candidate):
            if name in candidates and candidates[name] is not candidate:
                raise ValueError(f"ambiguous candidate alias: {name}")
            candidates[name] = candidate
    games = (seen or {}).get("games", {})
    for game in parsed.get("games", []):
        name = game["name"]
        canonical = _normalized_name(name)
        candidate = candidates.get(canonical, {})
        if ledger is not None and not candidate:
            raise ValueError(f"published game is missing from candidate ledger: {name}")
        pending_time = bool(game.get("is_time_unverified"))
        confirmed = not (game.get("is_watchlist") or game.get("is_update") or pending_time)
        videos = (candidate.get("videos") or [])
        judged = {decision_card.youtube_video_id(v.get("url", "")): v for v in videos}
        uncertain = game.get("time_status") == "unverified" or candidate.get("time_status") == "unverified"
        report_ids = set()
        for v in game.get("videos", []):
            key = decision_card.youtube_video_id(v.get("url", ""))
            if not key:
                continue  # The per-game YouTube URL gate names the missing source.
            report_ids.add(key)
            raw = by_id.get(key)
            evidence = judged.get(key)
            verdict = _time(evidence, modern) if evidence else None
            # A ledger must not promote an approximate raw result by copying an invented timestamp.
            if (raw == "unverified" or v.get("time_status") == "unverified"
                    or evidence and evidence.get("time_status") == "unverified"
                    or modern and raw != "verified" and verdict != "verified"):
                uncertain = True
        if confirmed and report_ids and report_ids <= prior_window:
            raise ValueError(f"{name}: 旧视频补核不能计入今日确认首发，请列更新或本地账本")
        if uncertain and not pending_time:
            raise ValueError(f"{name}: unverified 来源只能进入「待核实发现（时间存疑）」，不得计入确认首发")
        if pending_time:
            if game.get("upload_label"):
                raise ValueError(f"{name}: unverified 条目不能标精确上传时间")
            game["is_watchlist"] = True
        if candidate.get("classification") in {"Reject", "Product Lead"} or (
                candidate.get("classification") == "Pending" and not game.get("is_watchlist")):
            raise ValueError(f"{name}: {candidate['classification']} 不能进入此发布分区")
        original = candidate.get("renamed_from") or game.get("renamed_from")
        if original:
            if not game.get("is_update") or original not in games or not candidate.get("rename_evidence", "").strip():
                raise ValueError(f"{name}: 疑似更名必须有已知原名、具体依据且只能列为更新")
            game["renamed_from"] = original
        if modern and confirmed and seen is not None:
            aliases = _names(candidate) | {canonical}
            packages = set(candidate.get("package_ids", []))
            for known, record in games.items():
                if (aliases & (_names(record) | {_normalized_name(known)})
                        or packages.intersection(record.get("package_ids", []))):
                    if record.get("first_seen") != parsed.get("report_date"):
                        raise ValueError(f"{name}: 已有记录 {known}，不得按新品重复计数")
    return parsed
