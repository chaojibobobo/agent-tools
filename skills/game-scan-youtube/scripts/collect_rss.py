#!/usr/bin/env python3
"""Collect bounded, incremental YouTube evidence with an explicit coverage gate."""

import argparse
import json
import os
import re
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import youtube_transport as net


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


def _read_rss(channel, feed_dir, timeout, cache):
    cid = channel["channel_id"]
    if feed_dir is not None:
        return list(parse_entries((feed_dir / f"{cid}.xml").read_bytes(), channel)), "ok", None
    conditional = {}
    if isinstance(cache.get("xml"), str):
        if isinstance(cache.get("etag"), str) and cache["etag"]:
            conditional["If-None-Match"] = cache["etag"]
        if isinstance(cache.get("last-modified"), str) and cache["last-modified"]:
            conditional["If-Modified-Since"] = cache["last-modified"]
    status, headers, body = net.request(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}",
                                        timeout, headers=conditional)
    if status == 304 and conditional:
        return list(parse_entries(cache["xml"].encode("utf-8"), channel)), "not_modified", None
    net.check_status(status, headers)
    entries = list(parse_entries(body, channel))  # validate before caching
    fresh = {k: headers[k] for k in ("etag", "last-modified") if headers.get(k)}
    if fresh:
        fresh["xml"] = body.decode("utf-8")
    return entries, "ok", fresh


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
                "time_status": "unverified",
                "channel": {
                    "id": channel.get("id"),
                    "channel_id": channel.get("channel_id"),
                    "name": channel.get("name"),
                    "weight": int(channel.get("weight", 0)),
                },
            }
        )
    return videos


def coverage_summary(channels: List[dict], results: List[dict], threshold: float = .6) -> dict:
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
    if not effective or effective_rate < threshold or priority_rate < threshold:
        status = "blocked"
    elif exact == attempted:
        status = "complete"
    else:
        status = "degraded"
    return {
        "channels_attempted": attempted,
        "channels_succeeded": effective,
        "channels_failed": attempted - effective,
        "rss_succeeded": sum(item.get("status") == "rss_ok" or
                             (item.get("status") == "ok" and not item.get("fetch_tier"))
                             for item in results),
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
    if root.tag != f"{{{ATOM}}}feed":
        raise net.FetchError("expected an Atom feed")
    for entry in root.findall(f"{{{ATOM}}}entry"):
        video_id = child_text(entry, f"{{{YT}}}videoId")
        published_raw = child_text(entry, f"{{{ATOM}}}published")
        if not video_id:
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
            **_publication(published_raw),
            "updated": child_text(entry, f"{{{ATOM}}}updated"),
            "description": description,
            "views": views,
            "source_kind": "rss",
            "channel": {
                "id": channel.get("id"),
                "channel_id": channel.get("channel_id"),
                "name": channel.get("name"),
                "weight": int(channel.get("weight", 0)),
            },
        }


def _publication(raw):
    # A date-only string and the playlist's added-at time are NOT upload instants.
    if isinstance(raw, str) and re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)", raw
    ):
        try:
            return {"published": format_timestamp(parse_timestamp(raw)),
                    "time_status": "verified", "time_precision": "exact"}
        except ValueError:
            pass
    return {"time_status": "unverified", "time_precision": "unknown"}


def _video(channel, video_id, title, source, published=None, **extra):
    return {"video_id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}",
            "title": title or "", "source_kind": source, **_publication(published),
            "channel": {k: channel.get(k) for k in ("id", "channel_id", "name", "weight")},
            **extra}


def _load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def _disposable_cache(path):
    try:
        value = _load_json(path, {})
        return value if isinstance(value, dict) else {}
    except (UnicodeError, ValueError):
        return {}  # never trust an unreadable cache as evidence of an empty feed


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as tmp:
        try:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmp.write("\n")
            tmp.close()
            Path(tmp.name).replace(path)
        finally:
            Path(tmp.name).unlink(missing_ok=True)


def _seen_ids(workspace, state):
    ids = set((state or {}).get("seen_video_ids", []))
    for game in _load_json(workspace / "seen_games.json", {}).get("games", {}).values():
        for url in game.get("seen_videos", []):
            if not isinstance(url, str):
                continue
            parts = urlparse(url)
            if parts.hostname == "youtu.be":
                ids.add(parts.path.strip("/"))
            elif parts.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
                ids.update(parse_qs(parts.query).get("v", []))
                if parts.path.startswith(("/shorts/", "/live/", "/embed/")):
                    ids.add(parts.path.split("/")[2])
    return ids


def _pending_video_ids(workspace):
    history = _load_json(workspace / "candidate_history.json", {})
    return {
        vid for candidate in history.get("candidates", {}).values()
        for vid in (candidate.get("pending_video_ids") or [])
        if isinstance(vid, str) and vid
    }


def _error(exc):
    # FetchError is scrubbed at transport; exceptions from subprocess/third-party
    # content may include private URLs, so never persist their literal text.
    return str(exc) if isinstance(exc, net.FetchError) else type(exc).__name__


def _channel(channel, start, end, feed_dir, page_dir, fallback, timeout, retries,
             api_key, key_disabled, cache, pending_ids):
    cid = channel["channel_id"]
    result = {"channel_id": cid, "name": channel.get("name"),
              "rss_status": "not_attempted", "page_status": "not_attempted"}
    entries = None
    new_cache = None
    deadline = time.monotonic() + timeout

    def budget(slots=1):
        remaining = deadline - time.monotonic()
        if remaining <= .01:
            raise net.FetchError("channel deadline exceeded", retryable=True)
        return remaining / slots

    if not re.fullmatch(r"UC[A-Za-z0-9_-]+", cid):
        return [], dict(result, status="error", error="invalid UC channel ID"), None
    if api_key and not key_disabled.is_set():
        try:
            raw = net.uploads(cid, api_key, budget(3))
            entries = []
            for row in raw:
                details, snippet = row.get("contentDetails", {}), row.get("snippet", {})
                vid = details.get("videoId") or snippet.get("resourceId", {}).get("videoId")
                if vid:
                    entries.append(_video(channel, vid, snippet.get("title"), "api",
                                          details.get("videoPublishedAt"),
                                          description=snippet.get("description", ""), views=None))
            result.update(status="ok", fetch_tier="L1", page_status="not_needed")
        except (net.FetchError, ValueError, TypeError, AttributeError) as exc:
            if isinstance(exc, net.FetchError) and exc.no_retry_now:
                key_disabled.set()
            result["api_error"] = _error(exc)
            entries = None

    eligible_fallback = True
    if entries is None:
        for attempt in range(retries + 1):
            try:
                entries, rss_status, new_cache = _read_rss(channel, feed_dir,
                    budget(1 + (retries - attempt) + (2 if fallback else 0)), cache)
                result.update(status="rss_ok", fetch_tier="L2", rss_status=rss_status,
                              page_status="not_needed")
                break
            except (net.FetchError, OSError, ET.ParseError, ValueError) as exc:
                result.update(rss_status="error", rss_error=_error(exc))
                if isinstance(exc, net.FetchError):
                    eligible_fallback = exc.status in (None, 403, 429) or exc.status >= 500
                    if (exc.retryable and not exc.no_retry_now and attempt < retries and
                            time.monotonic() + .25 * 2**attempt < deadline):
                        time.sleep(.25 * 2**attempt)
                        continue
                break

    if entries is None and fallback and eligible_fallback and feed_dir is None and page_dir is None:
        try:
            rows = net.flat_playlist(cid, budget(2))
            flat = [_video(channel, row["id"], row.get("title"), "yt_dlp",
                           duration=row.get("duration")) for row in rows if row.get("id")]
            if not flat:
                raise net.FetchError("no flat video IDs")
            entries = flat
            result.update(status="page_ok", fetch_tier="L2.5", page_status="not_needed",
                          videos_page=len(entries))
        except (net.FetchError, ValueError, KeyError, TypeError, AttributeError) as exc:
            result["yt_dlp_error"] = _error(exc)

    if entries is None and fallback and eligible_fallback and (feed_dir is None or page_dir is not None):
        try:
            if page_dir is not None:
                body = (page_dir / f"{cid}.html").read_bytes()
            else:
                status, headers, body = net.request(f"https://www.youtube.com/channel/{cid}/videos", budget())
                net.check_status(status, headers)
            entries = parse_channel_page(body, channel)
            if not entries:
                raise net.FetchError("channel page has no recognizable videos")
            result.update(status="page_ok", fetch_tier="L3", page_status="ok", videos_page=len(entries))
        except (net.FetchError, OSError, ValueError, TypeError) as exc:
            result.update(page_status="error", page_error=_error(exc))
            entries = None

    if entries is None:
        result.update(status="error", error=result.get("page_error") or result.get("rss_error"))
        return [], result, None
    selected = []
    seven_day = 0
    for entry in entries:
        if entry["time_status"] == "unverified":
            selected.append(entry)
            continue
        published = parse_timestamp(entry["published"])
        if end - timedelta(days=7) <= published <= end:
            seven_day += 1
        if start <= published <= end:
            selected.append(entry)
        elif published < start and entry["video_id"] in pending_ids:
            # Day D had only relative time. Replay its now-dated evidence once
            # the feed supplies it, without backdating a new discovery into D+1.
            selected.append(dict(entry, verification_update=True))
    if result["fetch_tier"] in {"L1", "L2"}:
        result.update(videos_in_window=sum(v["time_status"] == "verified" and not v.get("verification_update")
                                           for v in selected),
                      videos_7d=seven_day)
    return selected, result, new_cache


def collect(
    channels_path: Path,
    now: datetime,
    min_weight: int,
    all_channels: bool,
    feed_dir: Optional[Path],
    timeout: int,
    state: Optional[dict] = None,
    max_backfill_hours: int = 72,
    channel_page_fallback: bool = True,
    channel_page_dir: Optional[Path] = None,
    *, health_threshold=.6, workers=5, retries=1, enrich_details=True,
) -> Dict:
    if not (0 <= health_threshold <= 1 and 1 <= workers <= 16 and
            retries in (1, 2) and 0 < timeout <= 120):
        raise ValueError("invalid health threshold [0,1], workers [1,16], retries [1,2] or timeout (0,120]")
    start, end, limited = resolve_window(now, state, max_backfill_hours)
    channels = load_channels(channels_path, min_weight, all_channels)
    # A channel present in both lists is one source, never two successful channels.
    unique = {}
    for channel in channels:
        unique.setdefault(channel["channel_id"], channel)
    channels = list(unique.values())
    cache_path = channels_path.parent / "rss_http_cache.json"
    cache = {}
    api_key = os.environ.get("YOUTUBE_API_KEY") if feed_dir is None else None
    key_disabled = threading.Event()
    results, videos = [], {}
    probe_error, updated = "", False
    try:
        net.probe(timeout, feed_dir)
    except net.FetchError as exc:
        probe_error = "L0: " + _error(exc)
    if probe_error:
        results = [{"channel_id": c["channel_id"], "name": c.get("name"), "status": "error",
                    "rss_status": "not_attempted", "page_status": "not_attempted",
                    "error": "L0 failed; channel not requested"} for c in channels]
    else:
        pending_ids = _pending_video_ids(channels_path.parent)
        if feed_dir is None:
            cache = _disposable_cache(cache_path)
        def fetch(ch):
            cached = cache.get(ch["channel_id"])
            return _channel(ch, start, end, feed_dir, channel_page_dir,
                            channel_page_fallback or channel_page_dir is not None, timeout, retries,
                            api_key, key_disabled, cached if isinstance(cached, dict) else {}, pending_ids)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for entries, result, refreshed in pool.map(fetch, channels):
                results.append(result)
                if refreshed is not None:
                    cache[result["channel_id"]] = refreshed
                    updated = True
                for entry in entries:
                    if entry["video_id"] not in videos or entry["time_status"] == "verified":
                        videos[entry["video_id"]] = entry
    if updated:
        # Store the response BODY with its validators. A 304 after a failed scan,
        # failed commit or wider backfill is replayed against the current window.
        _write_json(cache_path, cache)
    videos = sorted(videos.values(), key=lambda v: v.get("published", ""), reverse=True)
    summary = coverage_summary(channels, results, health_threshold)
    reason = probe_error
    active, succeeded = len(channels), summary["channels_succeeded"]
    rate = succeeded / active if active else 0.
    if not reason and summary["status"] == "blocked":
        reason = ("L1–L3: no active channels" if not active else
                  "L1–L3: effective or priority coverage below threshold / no successful channels")
    if not reason and api_key and not key_disabled.is_set() and enrich_details:
        detail_path = channels_path.parent / "video_detail_cache.json"
        details = _disposable_cache(detail_path)
        for video in videos:
            item = details.get(video["video_id"])
            if isinstance(item, dict):
                video.update({k: item[k] for k in ("views", "duration") if k in item})
        additions = net.api_details(videos, _seen_ids(channels_path.parent, state) | set(details) | pending_ids,
                                    api_key, min(timeout, 5))
        if additions:
            details.update(additions)
            # A bounded enrichment cache is NOT a discovery/commit cursor.
            _write_json(detail_path, dict(list(details.items())[-4096:]))
    tiers = {tier: sum(r.get("fetch_tier") == tier for r in results)
             for tier in ("L1", "L2", "L2.5", "L3")}
    return {
        "schema_version": 2,
        "generated_at": format_timestamp(datetime.now(timezone.utc)),
        "window": {"start": format_timestamp(start), "end": format_timestamp(end),
                   "hours": round((end - start).total_seconds() / 3600, 3),
                   "backfill_limited": limited, "max_backfill_hours": max_backfill_hours},
        "run_status": "RUN_DEGRADED" if reason else "RUN_OK",
        "source_summary": summary,
        "source_health": {"status": "degraded" if reason else "healthy",
                          "probe_status": "failed" if probe_error else ("fixture" if feed_dir else "ok"),
                          "active_channels": active, "succeeded_channels": succeeded,
                          "failed_channels": active - succeeded, "healthy_rate": rate,
                          "healthy_threshold": health_threshold, "fetch_tier_breakdown": tiers,
                          "degraded_reason": reason or None},
        "channel_results": results,
        "videos": videos,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channels", default="channels.json", help="Path to channels.json (also selects workspace .env)")
    parser.add_argument("--output", help="Output scan-input JSON; omit for --preflight-only")
    parser.add_argument("--env", help="Explicit .env path; process environment takes precedence")
    parser.add_argument("--preflight-only", action="store_true", help="Check configuration and L0 only, without writing state")
    parser.add_argument("--min-weight", type=int, default=3)
    parser.add_argument("--all-channels", action="store_true")
    parser.add_argument("--feed-dir", help="Fixture directory containing CHANNEL_ID.xml")
    parser.add_argument("--now", help="UTC/offset ISO timestamp; defaults to current time")
    parser.add_argument("--timeout", type=int, default=20, help="Entire per-channel deadline, seconds (1–120)")
    parser.add_argument("--workers", type=int, default=5, help="Bound concurrent channel workers (1–16)")
    parser.add_argument("--retries", type=int, choices=(1, 2), default=1)
    parser.add_argument("--health-threshold", "--healthy-threshold", type=float, default=None)
    parser.add_argument("--no-enrich-details", action="store_true", help="Skip optional batched duration/views for new API videos")
    parser.add_argument("--state", help="Path to scan_state.json")
    parser.add_argument("--max-backfill-hours", type=int, default=72)
    parser.add_argument("--channel-page-fallback", action="store_true", default=True,
                        help="Enable yt-dlp and channel-page fallback (default)")
    parser.add_argument("--no-channel-page-fallback", dest="channel_page_fallback", action="store_false")
    parser.add_argument("--channel-page-dir", help="Fixture directory containing CHANNEL_ID.html")
    args = parser.parse_args()

    channels_path = Path(args.channels).expanduser()
    loaded = net.load_env(channels_path.parent, Path(args.env) if args.env else None)
    feed_dir = Path(args.feed_dir).expanduser() if args.feed_dir else None
    threshold = args.health_threshold
    if threshold is None:
        threshold = float(os.environ.get("GAME_SCAN_HEALTH_THRESHOLD", ".6"))
    if args.preflight_only:
        probe_error = None
        try:
            net.probe(args.timeout, feed_dir)
        except net.FetchError as exc:
            probe_error = "L0: " + _error(exc)
        print(json.dumps({"probe_status": "failed" if probe_error else ("fixture" if feed_dir else "ok"),
                          "degraded_reason": probe_error, "env_file_loaded": bool(loaded),
                          "proxy_configured": any(os.environ.get(k) for k in
                              ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")),
                          "api_key_configured": bool(os.environ.get("YOUTUBE_API_KEY")),
                          "yt_dlp_available": bool(net.find_ytdlp())}, ensure_ascii=False))
        return 1 if probe_error else 0
    if not args.output:
        parser.error("--output is required except with --preflight-only")
    now = parse_timestamp(args.now) if args.now else datetime.now(timezone.utc)
    output = Path(args.output).expanduser()
    state = None
    if args.state:
        state_path = Path(args.state).expanduser()
        if state_path.exists():
            state = json.loads(state_path.read_text())
    data = collect(
        channels_path,
        now,
        args.min_weight,
        args.all_channels,
        feed_dir,
        args.timeout,
        state,
        args.max_backfill_hours,
        args.channel_page_fallback,
        Path(args.channel_page_dir).expanduser() if args.channel_page_dir else None,
        health_threshold=threshold, workers=args.workers, retries=args.retries,
        enrich_details=not args.no_enrich_details,
    )
    _write_json(output, data)

    summary = data["source_summary"]
    print(
        f"{data['run_status']} YouTube evidence: {len(data['videos'])} videos; "
        f"{summary['channels_succeeded']}/{summary['channels_attempted']} channels succeeded; "
        f"output={output}"
    )
    return 1 if data["run_status"] == "RUN_DEGRADED" else 0


if __name__ == "__main__":
    sys.exit(main())
