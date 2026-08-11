"""Build compact decision-first Feishu presentation payloads."""

import re
from datetime import date
from typing import Optional


STRICT_4X_PROFILE = "4x-slg-strict-v1"


def validate_4x_profile(parsed: dict, require_strict: bool = False) -> None:
    profile = parsed.get("scan_profile", "").strip().lower()
    if require_strict and profile != STRICT_4X_PROFILE:
        raise ValueError(
            "report must declare Scan Profile: 4X-SLG-STRICT-v1"
        )
    if profile != STRICT_4X_PROFILE:
        return

    invalid = []
    for game in parsed.get("games", []):
        fit = game.get("four_x_fit", "").strip().lower()
        allowed = fit.startswith("core")
        if game.get("is_watchlist"):
            allowed = allowed or fit.startswith("pending")
        if not allowed:
            invalid.append(game.get("name", "<unnamed>"))

    if invalid:
        raise ValueError(
            "strict 4X-SLG profile rejected non-core entries: "
            + ", ".join(invalid)
        )


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
    validate_4x_profile(parsed)
    games = parsed.get("games", [])
    confirmed = []
    for game in games:
        if game.get("is_update") or game.get("is_watchlist"):
            continue
        source_url = _youtube_source(game)
        if not source_url:
            raise ValueError(
                f"Confirmed new game has no YouTube source: {game['name']}"
            )
        confirmed.append({**game, "source_url": source_url})
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


def _tag(content: str, color: str) -> dict:
    return {
        "tag": "text_tag",
        "text": {"tag": "plain_text", "content": content},
        "color": color,
    }


def _markdown(content: str) -> dict:
    return {
        "tag": "div",
        "text": {"tag": "lark_md", "content": content},
    }


def _button(label: str, url: str, primary: bool = False) -> dict:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": "primary" if primary else "default",
        "url": url,
    }


def _youtube_source(game: dict) -> Optional[str]:
    for video in game.get("videos", []):
        url = video.get("url", "").strip()
        if re.match(
            r"https?://(?:(?:www|m)\.)?youtube\.com/(?:watch\?|live/)",
            url,
            flags=re.IGNORECASE,
        ) or re.match(
            r"https?://(?:www\.)?youtu\.be/",
            url,
            flags=re.IGNORECASE,
        ):
            return url
    return None


def _download_target(game: dict) -> Optional[str]:
    raw = game.get("download_url", "")
    if not raw:
        return None
    markdown_link = re.search(r"\[[^\]]+\]\((https?://[^\s)]+)\)", raw)
    if markdown_link:
        return markdown_link.group(1)
    raw_link = re.search(r"https?://[^\s)]+", raw)
    return raw_link.group(0) if raw_link else None


def _count_tags(model: dict) -> list:
    candidates = [
        (len(model["focus"]), "必看", "orange"),
        (len(model["supplemental"]), "补充", "blue"),
        (len(model["watchlist"]), "观察", "neutral"),
        (len(model["updates"]), "更新", "turquoise"),
    ]
    return [
        _tag(f"{count} {label}", color)
        for count, label, color in candidates
        if count
    ][:3]


def _focus_actions(game: dict) -> list:
    actions = []
    source_url = game.get("source_url") or _youtube_source(game)
    if source_url:
        actions.append(_button("YouTube 原视频", source_url, primary=True))
    download_url = _download_target(game)
    if download_url:
        actions.append(_button("看商店", download_url))
    return actions


def build_interactive_card(
    report_date: date,
    parsed: dict,
    summary: dict,
) -> dict:
    model = build_decision_model(parsed)
    if parsed.get("is_quiet"):
        period = summary.get("period", "")
        recent_count = summary.get("total_games", 0)
        detail = f"过去 7 天记录 {recent_count} 款新游戏"
        if period:
            detail += f"（{period}）"
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "grey",
                "title": {
                    "tag": "plain_text",
                    "content": "今日无重点｜Quiet Day",
                },
            },
            "elements": [
                _markdown(f"**结论**\n24 小时内没有值得立即展开的新项目。"),
                _markdown(f"**7 日回看**\n{detail}"),
            ],
        }

    elements = [
        _markdown(f"**结论**\n{model['conclusion']}"),
        {"tag": "hr"},
    ]
    for focus in model["focus"]:
        elements.append(
            _markdown(
                f"**🔥 必看｜{focus['name']}**\n"
                f"为什么看：{focus['why']}\n"
                f"核心机制：{focus['mechanism']}"
            )
        )
        actions = _focus_actions(focus)
        if actions:
            elements.append({"tag": "action", "actions": actions})

    if model["supplemental"]:
        supplemental = "\n".join(
            f"• {game['name']}｜"
            f"{_clip(game.get('hook') or game.get('status', ''), 26)}｜"
            f"[YouTube 原视频]({game['source_url']})"
            for game in model["supplemental"]
        )
        elements.append(_markdown(f"**➕ 补充**\n{supplemental}"))

    if model["trend"]:
        elements.append(_markdown(f"**趋势一句话**\n{model['trend']}"))

    if model["watchlist"]:
        watchlist_lines = []
        for game in model["watchlist"]:
            source_url = _youtube_source(game)
            if not source_url:
                raise ValueError(
                    f"Watchlist game has no YouTube source: {game['name']}"
                )
            watchlist_lines.append(
                f"◇ {game['name']}｜[YouTube 原视频]({source_url})"
            )
        elements.append(
            _markdown(f"**⚪ 观察**\n" + "\n".join(watchlist_lines))
        )

    if model["updates"]:
        update_lines = []
        for game in model["updates"]:
            source_url = _youtube_source(game)
            if not source_url:
                raise ValueError(
                    f"Updated game has no YouTube source: {game['name']}"
                )
            update_lines.append(
                f"↻ {game['name']}｜[YouTube 原视频]({source_url})"
            )
        elements.append(
            _markdown(f"**↻ 更新**\n" + "\n".join(update_lines))
        )

    header = {
        "template": "orange" if model["focus"] else "blue",
        "title": {
            "tag": "plain_text",
            "content": model["headline"],
        },
    }
    tags = _count_tags(model)
    if tags:
        header["text_tag_list"] = tags

    return {
        "config": {"wide_screen_mode": True},
        "header": header,
        "elements": elements,
    }


def _post_text(text: str) -> list:
    return [{"tag": "text", "text": text}]


def _post_links(game: dict) -> list:
    items = []
    source_url = game.get("source_url") or _youtube_source(game)
    if source_url:
        items.append({"tag": "a", "text": "YouTube 原视频", "href": source_url})
    download_url = _download_target(game)
    if download_url:
        if items:
            items.append({"tag": "text", "text": " · "})
        items.append({"tag": "a", "text": "看商店", "href": download_url})
    return items


def build_compact_post(
    report_date: date,
    parsed: dict,
    summary: dict,
) -> dict:
    model = build_decision_model(parsed)
    if parsed.get("is_quiet"):
        period = summary.get("period", "")
        title = "今日无重点｜Quiet Day"
        lines = [
            _post_text("结论｜24 小时内没有值得立即展开的新项目。"),
            _post_text(
                f"7 日回看｜{period} · {summary.get('total_games', 0)} 款新游戏"
            ),
        ]
        return {"zh_cn": {"title": title, "content": lines}}

    lines = [_post_text(f"结论｜{model['conclusion']}")]
    for focus in model["focus"]:
        lines.extend(
            [
                _post_text(f"🔥 必看｜{focus['name']}"),
                _post_text(f"为什么看｜{focus['why']}"),
                _post_text(f"核心机制｜{focus['mechanism']}"),
            ]
        )
        links = _post_links(focus)
        if links:
            lines.append(links)

    if model["supplemental"]:
        for index, game in enumerate(model["supplemental"][:4]):
            prefix = "➕ 补充｜" if index == 0 else "• "
            line = _post_text(
                f"{prefix}{game['name']}｜"
                f"{_clip(game.get('hook') or game.get('status', ''), 22)} · "
            )
            line.extend(_post_links(game))
            lines.append(line)
    if model["trend"]:
        lines.append(_post_text(f"趋势｜{model['trend']}"))

    if model["watchlist"]:
        for index, game in enumerate(model["watchlist"]):
            prefix = "⚪ 观察｜" if index == 0 else "◇ "
            line = _post_text(f"{prefix}{game['name']}｜")
            line.extend(_post_links(game))
            lines.append(line)

    if model["updates"]:
        for index, game in enumerate(model["updates"]):
            prefix = "↻ 更新｜" if index == 0 else "↻ "
            line = _post_text(f"{prefix}{game['name']}｜")
            line.extend(_post_links(game))
            lines.append(line)

    return {
        "zh_cn": {
            "title": model["headline"],
            "content": lines,
        }
    }


def visible_card_text(card: dict) -> str:
    lines = []
    header = card.get("header", {})
    title = (header.get("title") or {}).get("content")
    if title:
        lines.append(title)
    tags = [
        tag.get("text", {}).get("content", "")
        for tag in header.get("text_tag_list", [])
    ]
    if tags:
        lines.append(" ".join(f"[{tag}]" for tag in tags if tag))

    for element in card.get("elements", []):
        if element.get("tag") == "div":
            content = (element.get("text") or {}).get("content", "")
            if content:
                content = re.sub(
                    r"\[([^\]]+)\]\(https?://[^\s)]+\)",
                    r"\1",
                    content,
                )
                lines.extend(content.splitlines())
        elif element.get("tag") == "action":
            labels = [
                (action.get("text") or {}).get("content", "")
                for action in element.get("actions", [])
            ]
            if labels:
                lines.append(" ".join(f"[{label}]" for label in labels if label))
    return "\n".join(lines)


def visible_post_text(post: dict) -> str:
    lines = []
    zh_cn = post.get("zh_cn", {})
    title = zh_cn.get("title")
    if title:
        lines.append(title)
    for group in zh_cn.get("content", []):
        line = "".join(item.get("text", "") for item in group)
        if line:
            lines.append(line)
    return "\n".join(lines)
