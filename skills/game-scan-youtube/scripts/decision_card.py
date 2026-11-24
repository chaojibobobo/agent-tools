"""One validated editorial model for the interactive card and compact-post fallback."""

import re
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlsplit


STRICT_4X_PROFILE = "4x-slg-strict-v1"
NO_TREND = "今日无明显跨产品趋势"


def _load_banned_phrases():
    """Read the two published policy rows; malformed/missing rules must not pass."""
    skill = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(encoding="utf-8")
    phrases = []
    for group in ("空泛评价", "防御性废话"):
        rows = [line for line in skill.splitlines() if line.startswith(f"- {group}：")]
        match = re.fullmatch(rf"- {group}：([^。，；,;]+)。", rows[0]) if len(rows) == 1 else None
        if not match:
            raise ValueError(f"SKILL.md 禁词表「{group}」缺失、重复或格式错误")
        words = [w.strip() for w in match[1].split("、")]
        if not all(words) or len(set(phrases + words)) != len(phrases) + len(words):
            raise ValueError(f"SKILL.md 禁词表「{group}」含空词或重复词")
        phrases.extend(words)
    return tuple(phrases)


# A broken editorial policy blocks publication, not L0 alerts or YouTube ID parsing.
_POLICY_ERROR = None
try:
    BANNED_PHRASES = _load_banned_phrases()
except (ValueError, OSError) as exc:
    BANNED_PHRASES = ()
    _POLICY_ERROR = str(exc)


class CardBudgetError(ValueError):
    """Only size failures can qualify for an explicitly archival, read-only preview."""


def validate_4x_profile(parsed: dict, require_strict: bool = False) -> None:
    profile = parsed.get("scan_profile", "").strip().lower()
    if require_strict and profile != STRICT_4X_PROFILE:
        raise ValueError("report must declare Scan Profile: 4X-SLG-STRICT-v1")
    if profile == STRICT_4X_PROFILE:
        for game in parsed.get("games", []):
            fit = game.get("four_x_fit", "").strip().lower()
            if not (fit.startswith("core") or (game.get("is_watchlist") and fit.startswith("pending"))):
                raise ValueError(f"strict 4X-SLG profile rejected non-core entry: {game['name']}")


def youtube_video_id(url: str, strict: bool = False) -> Optional[str]:
    """Reject look-alike hosts, profiles, playlist links, empty/ambiguous watch IDs."""
    if not isinstance(url, str) or re.search(r"[\s<>\[\]()]", url):
        return None
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or parts.username or parts.password or parts.port:
            return None
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if host in {"youtu.be", "www.youtu.be"}:
        video_id = parts.path[1:]
    elif host in {"www.youtube.com", "youtube.com", "m.youtube.com"}:
        if parts.path == "/watch":
            ids = parse_qs(parts.query).get("v", [])
            video_id = ids[0] if len(ids) == 1 else ""
        else:
            match = re.fullmatch(r"/(?:live|shorts)/([^/]+)", parts.path)
            video_id = match[1] if match else ""
    else:
        return None
    pattern = r"[A-Za-z0-9_-]{11}" if strict else r"[A-Za-z0-9_-]+"
    return video_id if re.fullmatch(pattern, video_id) else None


def _youtube_source(game, strict=False):
    return next((v["url"] for v in game.get("videos", []) if youtube_video_id(v.get("url"), strict)), None)


def has_card_fields(parsed):
    return (any(k in parsed for k in ("card_headline", "card_trend", "trend_products"))
            or any(any(k.startswith("card_") for k in game) for game in parsed.get("games", [])))


def _banned(field, text):
    if _POLICY_ERROR:
        raise ValueError(_POLICY_ERROR)
    for word in BANNED_PHRASES:
        if word in text:
            raise ValueError(f"{field} 命中禁词「{word}」")


def _field(name, value, limit=None, sentence=False):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 缺少文案")
    value = value.strip()
    _banned(name, value)
    if len(value.splitlines()) != 1:
        raise ValueError(f"{name} 必须为 1 行")
    if any(token in value for token in ("**", "](", "<at ", "<a ")):
        raise ValueError(f"{name} 只允许纯文本，链接由原视频字段生成")
    if limit is not None and len(value) > limit:
        raise CardBudgetError(f"{name} 第 {limit + 1} 字超出 {limit} 字上限（实际 {len(value)}）")
    if sentence and re.search(r"[。！？!?；;]\s*\S|[。！？!?]{2,}", value):
        raise ValueError(f"{name} 只能写 1 句")
    return value


def _time_uncertain(game):
    video_statuses = [v.get("time_status") for v in game.get("videos", [])]
    return bool(game.get("is_time_unverified") or game.get("time_status") == "unverified"
                or "unverified" in video_statuses)


def _cross(game):
    match = re.search(r"\d+", game.get("cross_ref", ""))
    return int(match.group()) if match else 0


def _priority(game):
    priority = game.get("priority", "").strip().lower()
    if priority in {"focus", "must watch", "p0", "p1", "重点"}:
        return "focus"
    if priority in {"track", "watch", "p2", "补充"}:
        return "track"
    return ""


def _source_count(game):
    return len(set(v["url"] for v in game.get("videos", []) if youtube_video_id(v.get("url"))))


def _source_fact(game):
    return f"{_cross(game)} 频道同时覆盖" if _cross(game) >= 2 else f"{_source_count(game)} 条实机来源"


def _legacy_mechanism(game):
    # Only complete clauses from old reports: never ellipsize or infer an unobserved bridge.
    hook = game.get("hook") or game.get("gameplay") or ""
    _banned(f"必看「{game['name']}」核心机制", hook)
    phrase = hook.split("用", 1)[-1] if " 用 " in hook or "用" in hook[:10] else hook
    for clause in re.split(r"[。；;]", phrase):
        clause = re.sub(r"\s*\+\s*", " → ", clause).strip()
        if clause and len(clause) <= 45 and len(clause.split("→")) <= 3:
            return clause
    return "证据缺口：历史报告无简短机制链"


def _legacy_form(game):
    # A conservative label when the archived author did not distinguish front/back-end.
    hook = game.get("hook", "")
    _banned(f"补充「{game['name']}」", hook)
    keywords = ("Auto Chess", "自动棋", "Roguelike TD", "塔防", "TD", "跑酷", "合成", "三选一")
    front = next((k for k in keywords if k.casefold() in hook.casefold()), None)
    base = "4X" if game.get("four_x_fit", "").lower().startswith("core") else "待核"
    return (front or "待核") + "+" + base


def _single_evidence(name, value):
    value = _field(f"必看「{name}」为什么看", value, 40, sentence=True)
    if re.search(r"[；;\n]|\s[·|｜]\s", value):
        raise ValueError(f"必看「{name}」为什么看只能写 1 条证据")
    return value


def _trend(parsed, summary):
    trend = _field("趋势", parsed.get("card_trend", NO_TREND), 35, sentence=True)
    if trend == NO_TREND:
        return trend
    # build_7day_summary supplies only D-6 .. D. Current report names need not be in state yet.
    eligible = {g["name"].strip().casefold() for g in parsed.get("games", []) if not _time_uncertain(g)}
    eligible.update(g["name"].strip().casefold() for g in summary.get("hot_games", []))
    products = parsed.get("trend_products", [])
    if not isinstance(products, list) or not all(isinstance(n, str) for n in products):
        raise ValueError("趋势样本须为产品名列表")
    names = {n.strip().casefold() for n in products if n.strip()}
    if len(names) < 2 or not names <= eligible:
        raise ValueError(f"趋势须有 ≥2 个不同的当日/近7日产品作证；否则写「{NO_TREND}」")
    return trend


def build_decision_model(parsed: dict, summary=None) -> dict:
    if parsed.get("run_status") == "RUN_DEGRADED":
        raise ValueError("RUN_DEGRADED 不得生成常规日报")
    validate_4x_profile(parsed)
    strict = parsed.get("card_v3_required") or has_card_fields(parsed)
    if not strict:
        # These were projected into the headline/trend in the historical writer.
        for field, takeaway in zip(("标题", "趋势"), parsed.get("takeaways", [])):
            _banned(field, takeaway)
    confirmed, watch, updates = [], [], []
    entities, new_sources = set(), set()
    for game in parsed.get("games", []):
        name = game.get("name")
        if not strict and isinstance(name, str):
            linked = re.fullmatch(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", name)
            if linked and youtube_video_id(linked[2]):
                name = linked[1]
        name = _field("游戏名", name)
        for key, label in (("card_evidence", "为什么看"), ("card_mechanism", "核心机制"),
                           ("card_form", "补充前端+基底"), ("card_signal", "补充信号")):
            if key in game:
                _field(f"{label}「{name}」", game[key])
        bucket = updates if game.get("is_update") else (watch if game.get("is_watchlist") else confirmed)
        if bucket is confirmed and _time_uncertain(game):
            raise ValueError(f"time_status: unverified 不得计入确认首发：{name}")
        if game.get("renamed_from") and bucket is not updates:
            raise ValueError(f"疑似更名须计入更新而非新品：{name}")
        # Check all sources before budget checks so archive previews cannot hide a missing link.
        source = _youtube_source(game, strict=bool(strict))
        if not source:
            raise ValueError(f"Missing valid YouTube source: {name}")
        if strict:
            names = {n.strip().casefold() for n in
                     [name, game.get("renamed_from"), *(game.get("aliases") or [])] if n}
            if names & entities:
                raise ValueError(f"报告产品重复：{name}；同款或更名应合并入一条")
            entities.update(names)
            if bucket is confirmed:
                for video in game.get("videos", []):
                    url = video.get("url", "")
                    video_id = youtube_video_id(url, strict=True)
                    if video_id is None:
                        continue
                    # A chapter-specific original can independently document two games.
                    params = parse_qs(urlsplit(url).query)
                    anchor = (params.get("t") or params.get("start") or [""])[0]
                    key = (video_id, anchor)
                    if key not in new_sources:
                        new_sources.add(key)
                        source = url
                        break
                else:
                    raise ValueError(f"新增游戏重复使用另一款的 YouTube 原片：{name}；提供独立实机链接或对应片段")
        bucket.append({**game, "name": name, "source_url": source})
    if parsed.get("is_quiet") and confirmed:
        raise ValueError("Quiet Day 与确认新增冲突")

    ranked = sorted(confirmed, key=lambda g: (_priority(g) == "focus", _cross(g), _source_count(g)), reverse=True)
    eligible = [g for g in ranked if _priority(g) != "track"]
    limit = 2 if sum(_priority(g) == "focus" for g in eligible) >= 2 else 1
    focus = eligible[:limit]
    supplement = [g for g in ranked if g not in focus]

    for g in focus:
        name = g["name"]
        g["why"] = _single_evidence(name, g.get("card_evidence", _source_fact(g)))
        mechanism = g.get("card_mechanism")
        if mechanism is None and not strict:
            mechanism = _legacy_mechanism(g)
        mechanism = _field(f"必看「{name}」核心机制", mechanism, 45)
        nodes = [p.strip() for p in mechanism.split("→")]
        if not all(nodes) or len(nodes) > 3 or (strict and re.search(r"->|⇒|➜|[；;]", mechanism)):
            raise ValueError(f"必看「{name}」核心机制须用 → 连接 1–3 个非空节点")
        g["mechanism"] = mechanism

    for g in supplement:
        name = g["name"]
        form = _field(f"补充「{name}」前端+基底",
                      g.get("card_form") if strict else _legacy_form(g))
        source_fact = f"{_cross(g)}频道" if _cross(g) else f"{_source_count(g)}条实机"
        signal = _field(f"补充「{name}」差异点/渠道信号", g.get("card_signal", source_fact), sentence=True)
        if "｜" in form + signal or not re.fullmatch(r"[^+＋]+[+＋][^+＋]+", form):
            raise ValueError(f"补充「{name}」格式须为 前端+基底｜1个差异点或渠道信号")
        g["copy"] = _field(f"补充「{name}」", f"{name}｜{form}｜{signal}", 35)

    if focus:
        game = focus[0]
        fact = _source_fact(game)
        title = f"【值得看】{game['name']}：{fact}"
        if len(title) > 25:
            title = f"【值得看】{len(focus)}款必看，{fact}" if len(focus) == 1 else f"【值得看】{len(confirmed)}款新增有实机"
    elif confirmed:
        title = f"【常规跟踪】{len(confirmed)}款新增有实机"
    elif watch or updates:
        title = f"【常规跟踪】{len(watch)}款观察，{len(updates)}款更新"
    else:
        title = "【今日可跳过】本轮未见新增"
    headline = _field("标题", parsed.get("card_headline", title), 25, sentence=True)
    verdict = re.fullmatch(r"【(值得看|常规跟踪|今日可跳过)】(.+)", headline)
    if not verdict:
        raise ValueError("标题必须是【值得看 / 常规跟踪 / 今日可跳过】之一 + 核心增量事实")

    counts = " · ".join(f"{len(bucket)} {label}" for bucket, label in
                        ((focus, "必看"), (supplement, "补充"), (watch, "观察"), (updates, "更新")) if bucket)
    model = {"focus": focus, "supplemental": supplement, "watchlist": watch, "updates": updates,
             "headline": headline, "conclusion": headline, "trend": _trend(parsed, summary or {}), "counts": counts}
    if parsed.get("is_quiet") and summary and "total_games" in summary:
        model["recap"] = f"近7日记录 {summary['total_games']} 款新游戏"
        if summary.get("period"):
            model["recap"] += f"（{summary['period']}）"
    return model


def _text(s):
    return {"tag": "text", "text": s}


def _link(label, url):
    return {"tag": "a", "text": label, "href": url}


def _rows(model):
    rows = []
    for game in model["focus"]:
        links = [_link("YouTube 原视频", game["source_url"])]
        store = re.search(r"https?://[^\s)<>\]]+", game.get("download_url") or "")
        if store:
            links.append(_link("看商店", store[0]))
        rows.extend([
            ("text", [_text("🔥 必看｜" + game["name"])]),
            ("text", [_text("为什么看：" + game["why"])]),
            ("text", [_text("核心机制：" + game["mechanism"])]),
            ("action", links),
        ])
    for game in model["supplemental"]:
        rows.append(("text", [_text("补充｜" + game["copy"] + "｜"), _link("YouTube 原视频", game["source_url"])]))
    if model.get("recap"):
        rows.append(("text", [_text("7日回看｜" + model["recap"])]))
    rows.append(("text", [_text("趋势｜" + model["trend"])]))
    watch, updates = model["watchlist"], model["updates"]
    uncertain = sum(_time_uncertain(g) for g in watch + updates)
    rows.append(("text", [_text(f"观察 {len(watch)}｜时间存疑 {uncertain}｜改名/更新 {len(updates)}")]))
    # Compact headline highlights one change in each lane; full lists stay in the local report.
    key_changes = []
    for kind, games in (("观察", watch), ("更新", updates)):
        if games:
            g = next((g for g in games if g.get("renamed_from")), games[0])
            if key_changes:
                key_changes.append(_text("；"))
            key_changes += [_text(f"{kind}："), _link(g["name"], g["source_url"])]
            if g.get("renamed_from"):
                key_changes.append(_text(f"（原名：{g['renamed_from']}）"))
    if key_changes:
        rows.append(("text", key_changes))
    return rows


def _card(model, rows):
    elements = []
    for kind, row in rows:
        if kind == "action":
            elements.append({"tag": "action", "actions": [
                {"tag": "button", "text": {"tag": "plain_text", "content": link["text"]},
                 "type": "primary" if i == 0 else "default", "url": link["href"]}
                for i, link in enumerate(row)]})
        else:
            content = "".join(item["text"] if item["tag"] == "text" else
                              f"[{item['text']}]({item['href']})" for item in row)
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})
    color = "orange" if model["focus"] else ("grey" if model["headline"].startswith("【今日可跳过】") else "blue")
    return {"config": {"wide_screen_mode": True},
            "header": {"template": color, "title": {"tag": "plain_text", "content": model["headline"]}},
            "elements": elements}


def visible_card_text(card: dict) -> str:
    lines = []
    title = card.get("header", {}).get("title", {}).get("content")
    if title:
        lines.append(title)
    for el in card.get("elements", []):
        if el.get("tag") == "div":
            lines.append(re.sub(r"\[([^\]]+)\]\(https?://[^\s)]+\)", r"\1", el["text"]["content"]))
        elif el.get("tag") == "action":
            lines.append(" · ".join(a["text"]["content"] for a in el["actions"]))
    return "\n".join(lines)


def visible_post_text(post: dict) -> str:
    content = post["zh_cn"]
    return "\n".join([content["title"], *("".join(i["text"] for i in row) for row in content["content"])])


def _check_visible(text):
    lines = text.splitlines()
    if len(lines) > 15:
        raise CardBudgetError(f"卡片正文第 16 行超出 15 行上限（实际 {len(lines)} 行）：{lines[15][:24]}")
    if len(text) > 500:
        line = text[:500].count("\n") + 1
        raise CardBudgetError(f"卡片正文第 {line} 行超出 500 字上限（实际 {len(text)} 字）")
    _banned("卡片正文", text)


def _validated_layout(parsed, summary=None):
    model = build_decision_model(parsed, summary)
    rows = _rows(model)
    card = _card(model, rows)
    post_rows = [([items[0], _text(" · "), items[1]] if kind == "action" and len(items) == 2 else items)
                 for kind, items in rows]
    post = {"zh_cn": {"title": model["headline"], "content": post_rows}}
    for text in (visible_card_text(card), visible_post_text(post)):
        _check_visible(text)
    return model, card, post


def validate_decision_copy(parsed: dict, summary=None) -> dict:
    return _validated_layout(parsed, summary)[0]


def build_interactive_card(report_date: date, parsed: dict, summary: dict) -> dict:
    return _validated_layout(parsed, summary)[1]


def build_compact_post(report_date: date, parsed: dict, summary: dict) -> dict:
    return _validated_layout(parsed, summary)[2]


def historical_preview(parsed, reason):
    """Non-sendable, read-only index when an old report cannot fit today's budget."""
    if has_card_fields(parsed) or parsed.get("card_v3_required") or not isinstance(reason, CardBudgetError):
        raise ValueError("仅旧版归档的尺寸冲突可生成历史文本索引")
    validate_4x_profile(parsed)
    lines = [f"[DRY RUN] 历史原视频索引（非 v3 卡片，不能发送）：{reason}"]
    for game in parsed.get("games", []):
        source = _youtube_source(game)
        if not source:
            raise ValueError(f"Missing valid YouTube source: {game['name']}")
        kind = "观察" if game.get("is_watchlist") else ("更新" if game.get("is_update") else "新增")
        lines.append(f"{kind}｜{game['name']}｜{source}")
    return "\n".join(lines)
