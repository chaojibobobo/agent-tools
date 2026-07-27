"""Build compact decision-first Feishu presentation payloads."""

import re
from typing import Optional


def _cross_ref_count(game: dict) -> int:
    match = re.search(r"\d+", game.get("cross_ref", ""))
    return int(match.group()) if match else 0


def _is_explicit_focus(game: dict) -> bool:
    return game.get("priority", "").strip().lower() in {
        "focus",
        "must watch",
        "p0",
        "p1",
        "重点",
    }


def _is_explicit_track(game: dict) -> bool:
    return game.get("priority", "").strip().lower() in {
        "track",
        "watch",
        "p2",
        "补充",
    }


def _clip(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip("，。； ") + "…"


def _duration_seconds(meta: str) -> Optional[int]:
    match = re.search(r"(?:^|·\s*)(\d{1,2}):(\d{2})(?:\s*·|$)", meta or "")
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _mechanism(game: dict) -> str:
    hook = game.get("hook", "")
    if "用" in hook:
        hook = hook.split("用", 1)[1]
    hook = re.sub(r"\s*\+\s*", " → ", hook)
    return _clip(
        hook or game.get("gameplay", "") or game.get("status", ""),
        44,
    )


def _why(game: dict) -> str:
    videos = game.get("videos", [])
    count = _cross_ref_count(game)
    durations = [_duration_seconds(video.get("meta", "")) for video in videos]
    parts = []
    if count >= 2:
        parts.append(f"{count} 频道同时覆盖")
    elif game.get("status"):
        parts.append(game["status"])
    if videos:
        if durations and all(
            value is not None and value >= 600 for value in durations
        ):
            parts.append(f"{len(videos)} 条视频全部 ≥10 分钟")
        else:
            parts.append(f"{len(videos)} 条完整 gameplay")
    if not parts and game.get("store_signal"):
        parts.append(_clip(game["store_signal"], 34))
    return " · ".join(parts[:2])


def build_decision_model(parsed: dict) -> dict:
    games = parsed.get("games", [])
    confirmed = [
        game
        for game in games
        if not game.get("is_update") and not game.get("is_watchlist")
    ]
    watchlist = [game for game in games if game.get("is_watchlist")]
    updates = [game for game in games if game.get("is_update")]

    ranked = sorted(
        confirmed,
        key=lambda game: (
            _is_explicit_focus(game),
            _cross_ref_count(game),
            len(game.get("videos", [])),
        ),
        reverse=True,
    )
    focus_candidates = [
        game
        for game in ranked
        if _is_explicit_focus(game)
        or (_cross_ref_count(game) >= 2 and not _is_explicit_track(game))
    ]
    explicit_count = sum(_is_explicit_focus(game) for game in focus_candidates)
    focus_limit = 2 if explicit_count >= 2 else 1
    focus_raw = focus_candidates[:focus_limit]
    focus_ids = {id(game) for game in focus_raw}
    supplemental_raw = [game for game in ranked if id(game) not in focus_ids]

    focus = [
        {**game, "why": _why(game), "mechanism": _mechanism(game)}
        for game in focus_raw
    ]
    takeaways = parsed.get("takeaways", [])
    if focus:
        conclusion = _clip(
            takeaways[0] if takeaways else focus[0]["hook"],
            80,
        )
        headline = f"今日值得看｜{focus[0]['name']}"
    elif confirmed:
        conclusion = "今天有新增，但没有形成多频道或明确强信号。"
        headline = f"今日有新增｜{len(confirmed)} 款"
    else:
        conclusion = "今天没有值得立即展开的新项目。"
        headline = "今日无重点"

    trend = _clip(takeaways[1] if len(takeaways) > 1 else "", 60)
    count_parts = []
    for count, label in (
        (len(focus), "必看"),
        (len(supplemental_raw), "补充"),
        (len(watchlist), "观察"),
        (len(updates), "更新"),
    ):
        if count:
            count_parts.append(f"{count} {label}")

    return {
        "focus": focus,
        "supplemental": supplemental_raw,
        "watchlist": watchlist,
        "updates": updates,
        "headline": headline,
        "conclusion": conclusion,
        "trend": trend,
        "counts": " · ".join(count_parts),
    }
