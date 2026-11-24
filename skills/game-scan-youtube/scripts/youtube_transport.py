"""Bounded, credential-safe public YouTube transports (standard library + curl)."""

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

API = "https://www.googleapis.com/youtube/v3/"
PROBE = "https://www.youtube.com/generate_204"


def load_env(work_dir: Path, env_path: Optional[Path] = None):
    """Process settings win. Read one dotenv as data; never execute its contents."""
    explicit = env_path or os.environ.get("GAME_SCAN_ENV_FILE")
    paths = [Path(explicit).expanduser()] if explicit else [
        work_dir / ".env", Path.home() / ".game-scan-youtube" / ".env"
    ]
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, raw = line.strip().removeprefix("export ").partition("=")
            key = key.strip()
            if not sep or not re.fullmatch(
                r"(?i:(?:https?|all|no)_proxy)|YOUTUBE_API_KEY|YTDLP_BIN|(?:LARK|FEISHU|GAME_SCAN)_[A-Z0-9_]+", key
            ):
                continue
            try:
                parts = shlex.split(raw, comments=True)
            except ValueError:
                raise ValueError(f"invalid quoting in dotenv entry {key}") from None
            if len(parts) <= 1:
                os.environ.setdefault(key, parts[0] if parts else "")
        return path
    return None


class FetchError(Exception):
    def __init__(self, message, status=None, retryable=False, no_retry_now=False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.no_retry_now = no_retry_now


def request(url, timeout, *, headers=None):
    """Return status, lower-case headers and bytes. Wall clock bound includes DNS.

    Key/conditional headers travel on stdin, never argv; don't forward to redirects.
    Keep raw curl stderr out of persisted evidence (it may contain proxy credentials).
    """
    if timeout <= 0:
        raise FetchError("channel deadline exceeded", retryable=True)
    headers = headers or {}
    if any("\r" in v or "\n" in v for v in headers.values()):
        raise FetchError("invalid HTTP header")
    cmd = ["curl", "--disable", "--silent", "--max-time", str(timeout),
           "--max-filesize", "12582912", "--proto", "=https", "--globoff",
           "--write-out", "%{http_code}"]
    if headers:
        cmd += ["--header", "@-"]
    env = os.environ.copy()
    # curl deliberately ignores uppercase HTTP_PROXY; HTTPS/ALL_PROXY usually cover
    # HTTPS URLs. Honor HTTP_PROXY-only scheduled environments for these HTTPS calls.
    if not any(env.get(k) for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")):
        if env.get("HTTP_PROXY") or env.get("http_proxy"):
            env["https_proxy"] = env.get("HTTP_PROXY") or env["http_proxy"]
    try:
        with tempfile.TemporaryDirectory() as temp:
            header_file = Path(temp) / "headers"
            result = subprocess.run(
                cmd + ["--dump-header", str(header_file), url],
                input="".join(f"{k}: {v}\n" for k, v in headers.items()).encode(),
                capture_output=True, timeout=timeout, env=env
            )
            if result.returncode:
                raise FetchError(f"curl transport error {result.returncode}", retryable=True)
            raw = header_file.read_bytes()
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise FetchError(type(exc).__name__, retryable=True) from None
    fields = {}
    for line in raw.decode("iso-8859-1").splitlines():
        if line.startswith("HTTP/"):  # ignore proxy CONNECT headers
            fields.clear()
        elif ":" in line:
            name, val = line.split(":", 1)
            fields[name.lower()] = val.strip()
    return int(result.stdout[-3:]), fields, result.stdout[:-3]


def check_status(status, headers):
    if status != 200:
        raise FetchError(f"HTTP {status}", status, status == 429 or status >= 500,
                         no_retry_now=bool(headers.get("retry-after")))


def probe(timeout=5, feed_dir=None):
    if feed_dir is not None:  # fixture mode cannot touch any network
        if not feed_dir.is_dir():
            raise FetchError("offline feed fixture directory unavailable")
        return
    code, _, _ = request(PROBE, min(timeout, 5))
    if code != 204:
        raise FetchError(f"HTTP {code}", code)


def uploads(channel_id, api_key, timeout):
    if not re.fullmatch(r"UC[\w-]+", channel_id, re.ASCII):
        raise FetchError("invalid UC channel ID")
    query = urlencode({"playlistId": "UU" + channel_id[2:], "part": "snippet,contentDetails",
                       "maxResults": 50})
    status, headers, body = request(API + "playlistItems?" + query, timeout,
                                    headers={"X-Goog-Api-Key": api_key})
    if status in (400, 403, 429):
        try:
            reasons = [item.get("reason") for item in json.loads(body)["error"]["errors"]]
        except (ValueError, KeyError, TypeError, AttributeError):
            reasons = []
        if status == 429 or any(r in {"quotaExceeded", "dailyLimitExceeded", "keyInvalid",
                                      "accessNotConfigured", "rateLimitExceeded"} for r in reasons):
            raise FetchError("API key/quota unavailable", status, no_retry_now=True)
    check_status(status, headers)
    data = json.loads(body)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise FetchError("playlistItems response missing items")
    return data["items"]


def api_details(videos, seen, api_key, timeout):
    """Optional best-effort details, batched exclusively for *unseen* L1 videos."""
    fetched = {}
    todo = [v for v in videos if v["video_id"] not in seen and v["source_kind"] == "api"]
    for offset in range(0, len(todo), 50):
        batch = {v["video_id"]: v for v in todo[offset:offset + 50]}
        query = urlencode({"part": "contentDetails,statistics", "id": ",".join(batch)})
        try:
            status, headers, body = request(API + "videos?" + query, timeout,
                                            headers={"X-Goog-Api-Key": api_key})
            check_status(status, headers)
            for item in json.loads(body)["items"]:
                v = batch.get(item.get("id"))
                if v is not None:
                    fields = {}
                    if "duration" in item.get("contentDetails", {}):
                        fields["duration"] = item["contentDetails"]["duration"]
                    if "viewCount" in item.get("statistics", {}):
                        fields["views"] = int(item["statistics"]["viewCount"])
                    v.update(fields)
                    fetched[v["video_id"]] = fields
        except (FetchError, ValueError, KeyError, TypeError, AttributeError):
            break  # detail failure is not a lost channel; don't repeat a failing batch
    return fetched


def find_ytdlp() -> Optional[str]:
    """Honor a pinned executable; augment cron's lookup without changing process PATH."""
    command = os.path.expanduser(os.environ.get("YTDLP_BIN") or "yt-dlp")
    search_path = os.pathsep.join(filter(None, (os.environ.get("PATH", os.defpath),
                                                "/opt/homebrew/bin", "/usr/local/bin")))
    return shutil.which(command, path=search_path)


def flat_playlist(channel_id, timeout):
    executable = find_ytdlp()
    if not executable:
        raise FetchError("yt-dlp not found" + (" (check YTDLP_BIN)" if os.environ.get("YTDLP_BIN") else ""))
    try:
        result = subprocess.run(
            [executable, "--ignore-config", "--no-warnings", "--flat-playlist",
             "--dump-json", "--playlist-end", "8", "--retries", "0",
             "--extractor-retries", "0", "--socket-timeout", str(timeout),
             "--", f"https://www.youtube.com/channel/{channel_id}/videos"],
            capture_output=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise FetchError(f"yt-dlp {type(exc).__name__}") from None
    if result.returncode:
        raise FetchError(f"yt-dlp exit {result.returncode}")
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if not rows:
        raise FetchError("yt-dlp returned no usable videos")
    return rows[:8]
