"""Push Game Scout report to Feishu/Lark IM.

Usage:
  python3 push_feishu.py --date 2026-05-14
  python3 push_feishu.py --date 2026-05-14 --dry-run
  python3 push_feishu.py --date 2026-05-14 --dir ~/my-scout --env ~/my-scout/.env
"""

import json
import os
import re
import sys
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from decision_card import (
    build_compact_post,
    build_interactive_card,
    validate_4x_profile,
    visible_card_text,
)

BASE_URL = "https://open.feishu.cn/open-apis"


def post_json(url: str, payload: dict, headers: Optional[dict] = None, query: Optional[dict] = None) -> dict:
    if query:
        url = f"{url}?{urlencode(query)}"
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def load_env(env_path: Optional[Path] = None):
    """Load credentials from .env file and/or environment variables."""
    if env_path and env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

    missing = []
    for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_USER_OPEN_ID"):
        if not os.environ.get(key):
            missing.append(key)
    if missing:
        print(f"Missing env vars: {', '.join(missing)}")
        sys.exit(1)


def get_token() -> str:
    data = post_json(
        f"{BASE_URL}/auth/v3/tenant_access_token/internal/",
        {
            "app_id": os.environ["FEISHU_APP_ID"],
            "app_secret": os.environ["FEISHU_APP_SECRET"],
        },
    )
    if data.get("code") != 0:
        raise RuntimeError(f"Token request failed: {data}")
    return data["tenant_access_token"]


def parse_report(report_path: Path) -> dict:
    """Parse a daily markdown report into structured data."""
    text = report_path.read_text()
    result = {
        "is_quiet": False,
        "scan_profile": "",
        "games": [],
        "takeaways": [],
    }
    section_kind = None
    current_game = None
    dash = r"(?:—|--)"
    new_sections = {
        "new games",
        "new relevant games",
        "critical",
        "high",
        "medium",
        "videos to watch",
    }
    update_sections = {
        "new videos for known games",
        "seen games - new videos",
        "previously seen games with new activity",
    }

    def finish_game():
        nonlocal current_game
        if current_game and (current_game["videos"] or current_game["name"]):
            result["games"].append(current_game)
        current_game = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith(">") and "scan profile" in line.lower():
            profile_line = re.sub(r"[*_`]", "", line.lstrip("> "))
            _, separator, profile = profile_line.partition(":")
            if separator:
                result["scan_profile"] = profile.strip()
            continue

        if line.startswith("## "):
            finish_game()
            heading = re.sub(r"\s*\([^)]*\)\s*$", "", line[3:]).strip().lower()
            if heading.startswith("watchlist"):
                section_kind = "watchlist"
            elif any(heading.startswith(label) for label in new_sections):
                section_kind = "new"
            elif any(heading.startswith(label) for label in update_sections):
                section_kind = "update"
            elif heading == "what they are playing":
                section_kind = "takeaways"
            elif heading == "quiet day":
                section_kind = "quiet"
            else:
                section_kind = None
            continue

        if line.startswith("### ") and section_kind in {"new", "update", "watchlist"}:
            finish_game()
            header = re.sub(r"^\d+\.\s*", "", line[4:].strip())
            if "|" in header:
                header = header.split("|", 1)[1].strip()

            is_update = section_kind == "update"
            if is_update:
                game_name = re.sub(
                    rf"\s+{dash}\s+new video\s*$",
                    "",
                    header,
                    flags=re.IGNORECASE,
                ).strip()
                hook = "update"
            else:
                new_game_match = re.match(rf"(.+?)\s+{dash}\s+(.+)$", header)
                if new_game_match:
                    game_name = new_game_match.group(1).strip()
                    hook = new_game_match.group(2).strip()
                else:
                    game_name = header
                    hook = ""

            current_game = {
                "name": game_name,
                "hook": hook,
                "is_update": is_update,
                "is_watchlist": section_kind == "watchlist",
                "videos": [],
                "status": "",
                "developer": "",
                "platform": "",
                "gameplay": "",
                "download_url": "",
                "store_signal": "",
                "cross_ref": "",
                "priority": "",
                "four_x_fit": "",
                "four_x_evidence": "",
                "package_id": "",
                "aliases": [],
            }
            continue

        if current_game:
            # Video links: - [Title](url) — meta (also accepts historical "--").
            video_match = re.match(
                rf"-\s+\[(.+?)\]\((https?://[^\s)]+)\)(?:\s+{dash}\s+(.+))?",
                line,
            )
            if video_match:
                current_game["videos"].append({
                    "title": video_match.group(1),
                    "url": video_match.group(2),
                    "meta": (video_match.group(3) or "").strip(),
                })
                continue

            # Historical reports sometimes used a plain URL at the end of a bullet.
            raw_video_match = re.search(
                r"(https?://(?:www\.)?youtube\.com/watch\?v=[^\s)]+)",
                line,
            )
            if line.startswith("- ") and raw_video_match:
                url = raw_video_match.group(1)
                title = line[2:raw_video_match.start()].rstrip(" -—")
                current_game["videos"].append(
                    {"title": title or current_game["name"], "url": url, "meta": ""}
                )
                continue

            # Metadata lines: **Key:** Value
            meta_match = re.match(r"\*\*(.+?):\*\*\s*(.+)", line)
            if meta_match:
                key = meta_match.group(1).lower().replace(" ", "_")
                val = meta_match.group(2).strip()
                field_map = {
                    "status": "status",
                    "developer": "developer",
                    "platform": "platform",
                    "what_you'll_see": "gameplay",
                    "download": "download_url",
                    "store_signal": "store_signal",
                    "cross-ref": "cross_ref",
                    "priority": "priority",
                    "4x_fit": "four_x_fit",
                    "4x_evidence": "four_x_evidence",
                    "package_id": "package_id",
                    "package": "package_id",
                    "aliases": "aliases",
                }
                mapped = field_map.get(key)
                if mapped:
                    if mapped == "aliases":
                        current_game[mapped] = [
                            item.strip()
                            for item in re.split(r"[,，;；]", val)
                            if item.strip()
                        ]
                    else:
                        current_game[mapped] = val

        # Historical update summaries can be tables rather than H3 blocks.
        if section_kind == "update" and line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if (
                len(cells) >= 4
                and cells[0].lower() != "game"
                and not set(cells[0]) <= {"-", ":"}
            ):
                finish_game()
                result["games"].append(
                    {
                        "name": cells[0],
                        "hook": "update",
                        "is_update": True,
                        "is_watchlist": False,
                        "videos": [],
                        "status": "",
                        "developer": "",
                        "platform": "",
                        "gameplay": cells[3],
                        "download_url": "",
                        "store_signal": "",
                        "cross_ref": cells[2],
                        "priority": "",
                        "four_x_fit": "",
                        "four_x_evidence": "",
                        "package_id": "",
                        "aliases": [],
                    }
                )

        if section_kind == "takeaways":
            takeaway_match = re.match(r"(?:\d+[.)]|[-*])\s+(.+)", line)
            if takeaway_match:
                takeaway = takeaway_match.group(1).strip()
                takeaway = re.sub(r"\*\*(.+?)\*\*", r"\1", takeaway)
                takeaway = re.sub(r"`(.+?)`", r"\1", takeaway)
                takeaway = re.sub(r"([。！？])\s+", r"\1", takeaway)
                result["takeaways"].append(takeaway)

    finish_game()

    # Only mark as quiet if no games were parsed AND report contains Quiet Day header.
    # Reports can have both "## Quiet Day" and game updates (e.g. known-game new videos).
    if not result["games"] and "## Quiet Day" in text:
        result["is_quiet"] = True
    elif not result["games"]:
        raise ValueError(f"No games parsed and report is not a Quiet Day report: {report_path}")

    return result


def build_7day_summary(seen_games_path: Path, report_date: date, channels_path: Optional[Path] = None) -> dict:
    """Build 7-day summary from seen_games.json."""
    data = json.loads(seen_games_path.read_text())
    games = data.get("games", {})
    cutoff = report_date - timedelta(days=7)

    total_channels = 0
    if channels_path and channels_path.exists():
        ch = json.loads(channels_path.read_text())
        total_channels = len(ch.get("seed_channels", [])) + len(ch.get("discovered_channels", []))

    recent = []
    for name, info in games.items():
        first_str = info.get("first_seen", "")
        if not first_str:
            continue
        first = datetime.strptime(first_str, "%Y-%m-%d").date()
        if first >= cutoff:
            recent.append({
                "name": name,
                "developer": info.get("developer", "Unknown"),
                "videos": len(info.get("seen_videos", [])),
                "first_seen": info["first_seen"],
                "last_seen": info.get("last_seen", info["first_seen"]),
            })

    recent.sort(key=lambda g: g["videos"], reverse=True)

    return {
        "period": f"{cutoff.isoformat()[5:]} ~ {report_date.isoformat()[5:]}",
        "total_games": len(recent),
        "total_channels": total_channels,
        "hot_games": recent,
    }


def _build_legacy_feishu_content(
    report_date: date,
    parsed: dict,
    summary: dict,
) -> dict:
    """Build Feishu post content."""
    content_lines = []
    today = report_date.isoformat()

    if parsed["is_quiet"]:
        s = summary
        content_lines.append([
            {"tag": "text", "text": f"游戏竞品监控 | {today}\nQuiet Day — 24h 内无新增视频（≥10 分钟）\n\n"},
        ])
        content_lines.append([
            {"tag": "text", "text": f"📊 过去 7 天汇总 ({s['period']})\n"},
        ])
        content_lines.append([
            {"tag": "text", "text": f"  {s['total_games']} 款新游戏 · {sum(g['videos'] for g in s['hot_games'])} 条视频 · {s['total_channels']} 个频道监控"},
        ])

        if s["hot_games"]:
            content_lines.append([
                {"tag": "text", "text": "\n🔥 高信号游戏（视频覆盖数）："},
            ])
            for g in s["hot_games"][:9]:
                signal = "🔴" if g["videos"] >= 3 else "🟡"
                content_lines.append([
                    {"tag": "text", "text": f"  {signal} {g['name']} ({g['developer']}) — {g['videos']} 条视频"},
                ])

        content_lines.append([
            {"tag": "text", "text": f"\n\n库状态：{s['total_games']} 款游戏 · {s['total_channels']} 个频道"},
        ])

        return {
            "zh_cn": {
                "title": f"Game Scout | Quiet Day · {today[5:]}",
                "content": content_lines,
            }
        }

    # Normal day: judgment first, then strong signals, compact discoveries, watch/update.
    games = parsed["games"]
    confirmed_new = [
        game
        for game in games
        if not game.get("is_update") and not game.get("is_watchlist")
    ]
    watchlist = [game for game in games if game.get("is_watchlist")]
    updates = [game for game in games if game.get("is_update")]

    def cross_ref_count(game: dict) -> int:
        match = re.search(r"\d+", game.get("cross_ref", ""))
        return int(match.group()) if match else 0

    def display_cross_ref(game: dict) -> str:
        return re.sub(
            r"(\d+)\s*channels?",
            r"\1 频道",
            game.get("cross_ref", ""),
            flags=re.IGNORECASE,
        )

    def is_focus(game: dict) -> bool:
        explicit = game.get("priority", "").strip().lower()
        if explicit in {"focus", "must watch", "p0", "p1", "重点"}:
            return True
        if explicit in {"track", "watch", "p2", "补充"}:
            return False
        return cross_ref_count(game) >= 2

    focus_games = [game for game in confirmed_new if is_focus(game)][:3]
    focus_ids = {id(game) for game in focus_games}
    supplemental = [game for game in confirmed_new if id(game) not in focus_ids]

    title_parts = []
    if focus_games:
        title_parts.append(f"{len(focus_games)} 重点")
    if supplemental:
        title_parts.append(f"{len(supplemental)} 补充")
    if watchlist:
        title_parts.append(f"{len(watchlist)} 观察")
    if updates:
        title_parts.append(f"{len(updates)} 更新")
    title_summary = " · ".join(title_parts)

    def add_text(text: str):
        content_lines.append([{"tag": "text", "text": text}])

    def download_target(game: dict):
        raw_url = game.get("download_url", "")
        if not raw_url:
            return None
        markdown_link = re.match(r"\[.*?\]\((https?://[^\s)]+)\)", raw_url)
        url = markdown_link.group(1) if markdown_link else raw_url
        if "play.google.com" in url:
            label = "商店"
        elif "apps.apple.com" in url:
            label = "商店"
        else:
            label = "下载"
        return label, url

    def add_links(game: dict, max_videos: int):
        items = [{"tag": "text", "text": "  "}]
        for index, video in enumerate(game.get("videos", [])[:max_videos], start=1):
            if len(items) > 1:
                items.append({"tag": "text", "text": " · "})
            video_label = "视频" if max_videos == 1 else f"视频{index}"
            items.append({"tag": "a", "text": video_label, "href": video["url"]})
        download = download_target(game)
        if download:
            if len(items) > 1:
                items.append({"tag": "text", "text": " · "})
            items.append({"tag": "a", "text": download[0], "href": download[1]})
        if len(items) > 1:
            content_lines.append(items)

    add_text(f"游戏竞品监控｜{today}\n{title_summary}\n")
    add_text("今日判断")
    takeaways = parsed.get("takeaways", [])[:3]
    if not takeaways:
        if focus_games:
            names = "、".join(game["name"] for game in focus_games)
            takeaways = [f"今天优先看 {names}；其余发现先作为补充信号。"]
        else:
            takeaways = ["今天有新增，但没有形成多频道或明确高信号项目。"]
    for index, takeaway in enumerate(takeaways, start=1):
        add_text(f"{index}. {takeaway}")

    if focus_games:
        add_text("\n先看")
        for index, game in enumerate(focus_games, start=1):
            label = game.get("hook") or game.get("status") or "高信号新发现"
            add_text(f"{index}. {game['name']}｜{label}")
            store_signal = game.get("store_signal", "")
            cross_ref = display_cross_ref(game)
            count = cross_ref_count(game)
            store_has_same_count = bool(
                count
                and re.search(
                    rf"\b{count}\s*(?:channels?|个频道)",
                    store_signal,
                    flags=re.IGNORECASE,
                )
            )
            signal_parts = [part for part in (game.get("status"),) if part]
            if cross_ref and not store_has_same_count:
                signal_parts.append(cross_ref)
            if store_signal:
                signal_parts.append(store_signal)
            if signal_parts:
                add_text(f"  信号：{' · '.join(signal_parts)}")
            if game.get("gameplay"):
                add_text(f"  玩法：{game['gameplay']}")
            add_links(game, max_videos=2)

    if supplemental:
        add_text("\n其他新增")
        for game in supplemental:
            label = game.get("hook") or game.get("status") or "新发现"
            signal = display_cross_ref(game) or game.get("store_signal")
            suffix = f"｜{signal}" if signal else ""
            add_text(f"• {game['name']}｜{label}{suffix}")
            add_links(game, max_videos=1)

    if watchlist or updates:
        add_text("\n观察与更新")
        for game in watchlist:
            label = game.get("hook") or game.get("cross_ref") or "证据待补"
            add_text(f"◇ {game['name']}｜{label}")
            add_links(game, max_videos=1)
        for game in updates:
            add_text(f"↻ {game['name']}｜新增 {len(game.get('videos', []))} 条视频")
            add_links(game, max_videos=1)

    return {
        "zh_cn": {
            "title": f"Game Scout {today[5:]}｜{title_summary}",
            "content": content_lines,
        }
    }


def build_feishu_content(
    report_date: date,
    parsed: dict,
    summary: dict,
) -> dict:
    """Compatibility wrapper for the compact post fallback."""
    return build_compact_post(report_date, parsed, summary)


def push_report(
    card_content: dict,
    fallback_content: dict,
    dry_run: bool = False,
    request_fn=post_json,
    token_fn=get_token,
    receive_id: Optional[str] = None,
) -> dict:
    """Send an interactive card, with one compact-post fallback before receipt."""
    if dry_run:
        print("[DRY RUN] Type: interactive")
        print(visible_card_text(card_content))
        print()
        return {
            "status": "dry-run",
            "message_id": None,
            "message_format": "interactive",
            "fallback_used": False,
        }

    token = token_fn()
    target_open_id = receive_id or os.environ["FEISHU_USER_OPEN_ID"]
    common = {
        "headers": {"Authorization": f"Bearer {token}"},
        "query": {"receive_id_type": "open_id"},
    }
    card_data = request_fn(
        f"{BASE_URL}/im/v1/messages",
        {
            "receive_id": target_open_id,
            "msg_type": "interactive",
            "content": json.dumps(card_content),
        },
        **common,
    )
    if card_data.get("code") == 0:
        message_id = card_data["data"]["message_id"]
        return {
            "status": "sent",
            "message_id": message_id,
            "message_format": "interactive",
            "fallback_used": False,
        }

    unexpected_message_id = (card_data.get("data") or {}).get("message_id")
    if unexpected_message_id:
        raise RuntimeError(
            "card returned a message_id with a failure code; refusing fallback"
        )

    fallback_data = request_fn(
        f"{BASE_URL}/im/v1/messages",
        {
            "receive_id": target_open_id,
            "msg_type": "post",
            "content": json.dumps(fallback_content),
        },
        **common,
    )
    if fallback_data.get("code") != 0:
        raise RuntimeError(
            f"card failed: {card_data}; fallback failed: {fallback_data}"
        )
    message_id = fallback_data["data"]["message_id"]
    return {
        "status": "sent",
        "message_id": message_id,
        "message_format": "post",
        "fallback_used": True,
    }


def write_receipt(work_dir: Path, report_date: str, push_result: dict) -> Path:
    """Persist the actual delivered format together with its message id."""
    receipt_dir = work_dir / "receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / f"{report_date}.feishu.json"
    temporary = receipt_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "date": report_date,
                "status": push_result["status"],
                "message_id": push_result["message_id"],
                "message_format": push_result["message_format"],
                "fallback_used": push_result["fallback_used"],
                "sent_at": datetime.now().astimezone().isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    temporary.replace(receipt_path)
    return receipt_path


def main():
    parser = argparse.ArgumentParser(description="Push Game Scout report to Feishu")
    parser.add_argument("--date", required=True, help="Report date (YYYY-MM-DD)")
    parser.add_argument(
        "--dir",
        default=os.environ.get(
            "GAME_SCAN_WORK_DIR",
            "/Users/bobo/Codexspace/tools/game-scan-youtube",
        ),
        help="Working directory",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print message without sending")
    parser.add_argument("--env", default="~/.game-scan-youtube/.env", help="Path to .env file")
    args = parser.parse_args()

    work_dir = Path(args.dir).expanduser()
    env_path = Path(args.env).expanduser()
    report_date = datetime.strptime(args.date, "%Y-%m-%d").date()

    if not args.dry_run:
        load_env(env_path)

    report_path = work_dir / f"{args.date}.md"
    if not report_path.exists():
        print(f"Report not found: {report_path}")
        sys.exit(1)

    seen_games_path = work_dir / "seen_games.json"
    if not seen_games_path.exists():
        print(f"seen_games.json not found: {seen_games_path}")
        sys.exit(1)

    parsed = parse_report(report_path)
    validate_4x_profile(
        parsed,
        require_strict=report_date >= date(2026, 8, 6),
    )
    channels_path = work_dir / "channels.json"
    summary = build_7day_summary(seen_games_path, report_date, channels_path)
    card_content = build_interactive_card(report_date, parsed, summary)
    fallback_content = build_compact_post(report_date, parsed, summary)
    push_result = push_report(
        card_content,
        fallback_content,
        dry_run=args.dry_run,
    )
    if not args.dry_run:
        print(
            f"Pushed {push_result['message_format']}! "
            f"message_id: {push_result['message_id']}"
        )
        receipt_path = write_receipt(work_dir, args.date, push_result)
        print(f"Receipt: {receipt_path}")


if __name__ == "__main__":
    main()
