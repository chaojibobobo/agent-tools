#!/usr/bin/env python3
"""Collect deterministic YouTube RSS evidence before an LLM scan runs."""

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional


ATOM = "http://www.w3.org/2005/Atom"
YT = "http://www.youtube.com/xml/schemas/2015"
MEDIA = "http://search.yahoo.com/mrss/"


def parse_timestamp(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def resolve_window(
    now: datetime,
    state: Optional[dict],
    max_backfill_hours: int = 72,
):
    window_end = now.astimezone(timezone.utc)
    default_start = window_end - timedelta(hours=24)
    if not state or not state.get("last_successful_scan_end"):
        return default_start, window_end, False
    cursor = parse_timestamp(state["last_successful_scan_end"])
    oldest_allowed = window_end - timedelta(hours=max_backfill_hours)
    if cursor < oldest_allowed:
        return oldest_allowed, window_end, True
    return min(cursor, window_end), window_end, False


def load_channels(path: Path, min_weight: int, all_channels: bool) -> List[dict]:
    data = json.loads(path.read_text())
    channels = data.get("seed_channels", []) + data.get("discovered_channels", [])
    eligible = []
    for channel in channels:
        if not channel.get("channel_id"):
            continue
        if all_channels or int(channel.get("weight", 0)) >= min_weight:
            eligible.append(channel)
    return sorted(
        eligible,
        key=lambda channel: (-int(channel.get("weight", 0)), channel.get("name", "")),
    )


def read_feed(channel_id: str, feed_dir: Optional[Path], timeout: int) -> bytes:
    if feed_dir:
        return (feed_dir / f"{channel_id}.xml").read_bytes()

    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    result = subprocess.run(
        [
            "curl", "-fL", "-sS", "--max-time", str(timeout),
            "--retry", "2", "--retry-all-errors", "--retry-delay", "1", url,
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(error or f"curl exited {result.returncode}")
    return result.stdout


def read_channel_page(
    channel_id: str,
    channel_page_dir: Optional[Path],
    timeout: int,
) -> bytes:
    if channel_page_dir:
        return (channel_page_dir / f"{channel_id}.html").read_bytes()
    url = f"https://www.youtube.com/channel/{channel_id}/videos"
    result = subprocess.run(
        [
            "curl", "-fL", "-sS", "--max-time", str(timeout),
            "--retry", "2", "--retry-all-errors", "--retry-delay", "1", url,
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(error or f"curl exited {result.returncode}")
    return result.stdout


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _content(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("content"), str):
            return value["content"]
        if isinstance(value.get("simpleText"), str):
            return value["simpleText"]
        runs = value.get("runs")
        if isinstance(runs, list):
            return "".join(str(item.get("text", "")) for item in runs if isinstance(item, dict))
    return ""


def _find_strings(value) -> List[str]:
    strings = []

    def visit(item):
        if isinstance(item, str):
            strings.append(item)
        elif isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return list(dict.fromkeys(strings))


def _load_initial_data(html_bytes: bytes) -> dict:
    text = html_bytes.decode("utf-8", errors="replace")
    markers = ("var ytInitialData =", "window[\"ytInitialData\"] =")
    decoder = json.JSONDecoder()
    for marker in markers:
        start = text.find(marker)
        if start < 0:
            continue
        payload = text[start + len(marker):].lstrip()
        try:
            data, _ = decoder.raw_decode(payload)
            return data
        except json.JSONDecodeError:
            continue
    raise ValueError("ytInitialData not found in channel page")


def parse_channel_page(html_bytes: bytes, channel: dict) -> List[dict]:
    data = _load_initial_data(html_bytes)
    videos = []
    seen = set()
    for node in _walk(data):
        model = node.get("lockupViewModel")
        if not isinstance(model, dict):
            continue
        video_id = model.get("contentId")
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        strings = _find_strings(model)
        title = _content(model.get("title"))
        if not title:
            title = next((item for item in strings if len(item) > 4), "")
        duration = _content(model.get("duration"))
        if not duration:
            duration = next(
                (item for item in strings if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", item)),
                "",
            )
        relative = next(
            (
                item
                for item in strings
                if re.search(
                    r"(?:minute|hour|day|week|month|year)s? ago|(?:分钟前|小时前|天前|周前|个月前)",
                    item,
                    flags=re.IGNORECASE,
                )
            ),
            "",
        )
        videos.append(
            {
                "video_id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "title": title,
                "relative_published": relative,
                "duration": duration,
                "page_metadata": [item for item in strings if item not in {title, duration}],
                "source_kind": "channel_page",
                "time_precision": "relative",
                "channel": {
                    "id": channel.get("id"),
                    "channel_id": channel.get("channel_id"),
                    "name": channel.get("name"),
                    "weight": int(channel.get("weight", 0)),
                },
            }
        )
    return videos


def coverage_summary(channels: List[dict], results: List[dict]) -> dict:
    result_map = {item.get("channel_id"): item for item in results}
    attempted = len(channels)
    effective = sum(
        result_map.get(channel.get("channel_id"), {}).get("status")
        in {"rss_ok", "page_ok", "ok"}
        for channel in channels
    )
    exact = sum(
        result_map.get(channel.get("channel_id"), {}).get("status") in {"rss_ok", "ok"}
        for channel in channels
    )
    priority = [channel for channel in channels if int(channel.get("weight", 0)) >= 7]
    priority_base = priority or channels
    priority_effective = sum(
        result_map.get(channel.get("channel_id"), {}).get("status")
        in {"rss_ok", "page_ok", "ok"}
        for channel in priority_base
    )
    effective_rate = effective / attempted if attempted else 0.0
    exact_rate = exact / attempted if attempted else 0.0
    priority_rate = priority_effective / len(priority_base) if priority_base else 0.0
    if effective_rate < 0.60 or priority_rate < 0.60:
        status = "blocked"
    elif exact == attempted:
        status = "complete"
    else:
        status = "degraded"
    return {
        "channels_attempted": attempted,
        "channels_succeeded": effective,
        "channels_failed": attempted - effective,
        "rss_succeeded": exact,
        "page_fallback_succeeded": sum(
            item.get("status") == "page_ok" for item in results
        ),
        "effective_coverage": round(effective_rate, 3),
        "exact_coverage": round(exact_rate, 3),
        "priority_effective_coverage": round(priority_rate, 3),
        "status": status,
    }


def child_text(node: ET.Element, path: str) -> str:
    child = node.find(path)
    return (child.text or "").strip() if child is not None else ""


def parse_entries(xml_bytes: bytes, channel: dict) -> Iterable[dict]:
    root = ET.fromstring(xml_bytes)
    for entry in root.findall(f"{{{ATOM}}}entry"):
        video_id = child_text(entry, f"{{{YT}}}videoId")
        published_raw = child_text(entry, f"{{{ATOM}}}published")
        if not video_id or not published_raw:
            continue
        media_group = entry.find(f"{{{MEDIA}}}group")
        description = ""
        views = None
        if media_group is not None:
            description = child_text(media_group, f"{{{MEDIA}}}description")
            community = media_group.find(f"{{{MEDIA}}}community")
            if community is not None:
                statistics = community.find(f"{{{MEDIA}}}statistics")
                if statistics is not None and statistics.get("views"):
                    try:
                        views = int(statistics.get("views"))
                    except (TypeError, ValueError):
                        views = None

        yield {
            "video_id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "title": child_text(entry, f"{{{ATOM}}}title"),
            "published": format_timestamp(parse_timestamp(published_raw)),
            "updated": child_text(entry, f"{{{ATOM}}}updated"),
            "description": description,
            "views": views,
            "source_kind": "rss",
            "time_precision": "exact",
            "channel": {
                "id": channel.get("id"),
                "channel_id": channel.get("channel_id"),
                "name": channel.get("name"),
                "weight": int(channel.get("weight", 0)),
            },
        }


def collect(
    channels_path: Path,
    now: datetime,
    min_weight: int,
    all_channels: bool,
    feed_dir: Optional[Path],
    timeout: int,
    state: Optional[dict] = None,
    max_backfill_hours: int = 72,
    channel_page_fallback: bool = False,
    channel_page_dir: Optional[Path] = None,
) -> Dict:
    window_start, window_end, backfill_limited = resolve_window(
        now, state, max_backfill_hours
    )
    channels = load_channels(channels_path, min_weight, all_channels)
    videos = []
    channel_results = []

    for channel in channels:
        channel_id = channel["channel_id"]
        try:
            feed = read_feed(channel_id, feed_dir, timeout)
            entries = list(parse_entries(feed, channel))
            in_window = [
                entry
                for entry in entries
                if window_start <= parse_timestamp(entry["published"]) <= window_end
            ]
            seven_day_count = sum(
                1
                for entry in entries
                if window_end - timedelta(days=7)
                <= parse_timestamp(entry["published"])
                <= window_end
            )
            videos.extend(in_window)
            channel_results.append(
                {
                    "channel_id": channel_id,
                    "name": channel.get("name"),
                    "status": "rss_ok",
                    "rss_status": "ok",
                    "page_status": "not_needed",
                    "videos_in_window": len(in_window),
                    "videos_7d": seven_day_count,
                }
            )
        except Exception as exc:
            rss_error = str(exc)
            if channel_page_fallback or channel_page_dir:
                try:
                    page = read_channel_page(channel_id, channel_page_dir, timeout)
                    page_entries = parse_channel_page(page, channel)
                    videos.extend(page_entries)
                    channel_results.append(
                        {
                            "channel_id": channel_id,
                            "name": channel.get("name"),
                            "status": "page_ok",
                            "rss_status": "error",
                            "page_status": "ok",
                            "rss_error": rss_error,
                            "videos_page": len(page_entries),
                        }
                    )
                    continue
                except Exception as page_exc:
                    page_error = str(page_exc)
            else:
                page_error = "not attempted"
            channel_results.append(
                {
                    "channel_id": channel_id,
                    "name": channel.get("name"),
                    "status": "error",
                    "rss_status": "error",
                    "page_status": "error" if (channel_page_fallback or channel_page_dir) else "not_attempted",
                    "rss_error": rss_error,
                    "page_error": page_error,
                    "error": rss_error,
                }
            )

    deduped = {}
    for video in videos:
        existing = deduped.get(video["video_id"])
        if existing is None or video.get("time_precision") == "exact":
            deduped[video["video_id"]] = video
    videos = list(deduped.values())
    videos.sort(key=lambda entry: entry.get("published", ""), reverse=True)
    summary = coverage_summary(channels, channel_results)
    return {
        "schema_version": 2,
        "generated_at": format_timestamp(datetime.now(timezone.utc)),
        "window": {
            "start": format_timestamp(window_start),
            "end": format_timestamp(window_end),
            "hours": round((window_end - window_start).total_seconds() / 3600, 3),
            "backfill_limited": backfill_limited,
            "max_backfill_hours": max_backfill_hours,
        },
        "source_summary": summary,
        "channel_results": channel_results,
        "videos": videos,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channels", required=True, help="Path to channels.json")
    parser.add_argument("--output", required=True, help="Output scan-input JSON")
    parser.add_argument("--min-weight", type=int, default=3)
    parser.add_argument("--all-channels", action="store_true")
    parser.add_argument("--feed-dir", help="Fixture directory containing CHANNEL_ID.xml")
    parser.add_argument("--now", help="UTC/offset ISO timestamp; defaults to current time")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--state", help="Path to scan_state.json")
    parser.add_argument("--max-backfill-hours", type=int, default=72)
    parser.add_argument("--channel-page-fallback", action="store_true")
    parser.add_argument("--channel-page-dir", help="Fixture directory containing CHANNEL_ID.html")
    args = parser.parse_args()

    now = parse_timestamp(args.now) if args.now else datetime.now(timezone.utc)
    output = Path(args.output).expanduser()
    state = None
    if args.state:
        state_path = Path(args.state).expanduser()
        if state_path.exists():
            state = json.loads(state_path.read_text())
    data = collect(
        Path(args.channels).expanduser(),
        now,
        args.min_weight,
        args.all_channels,
        Path(args.feed_dir).expanduser() if args.feed_dir else None,
        args.timeout,
        state,
        args.max_backfill_hours,
        args.channel_page_fallback,
        Path(args.channel_page_dir).expanduser() if args.channel_page_dir else None,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(output)

    summary = data["source_summary"]
    print(
        f"YouTube evidence: {len(data['videos'])} videos; "
        f"{summary['channels_succeeded']}/{summary['channels_attempted']} channels succeeded; "
        f"output={output}"
    )
    return 1 if summary["status"] == "blocked" else 0


if __name__ == "__main__":
    sys.exit(main())
