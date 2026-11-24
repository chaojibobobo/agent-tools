#!/usr/bin/env python3
"""High-yield, quota-based image sourcing for game-art-sourcing.

The module keeps network collection, local quality control, and report metadata
separate so every network-facing function can be unit-tested with mocks.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps, ImageStat

try:  # Registers AVIF with Pillow on builds without native AVIF support.
    import pillow_avif  # type: ignore  # noqa: F401
except ImportError:  # Validation reports decode_failed instead of passing bad bytes.
    pillow_avif = None


log = logging.getLogger("game-art-image-sourcing")

BUCKETS = ("key_art", "character", "environment", "ui_gameplay")
CORE_BUCKETS = ("character", "environment", "ui_gameplay")
DEFAULT_QUOTAS = {
    "key_art": 2,
    "character": 4,
    "environment": 4,
    "ui_gameplay": 3,
}
DEFAULT_MIN_TOTAL = 8
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0 Safari/537.36"
)
DOMAIN_REFERERS = {
    "artstation.com": "https://www.artstation.com/",
    "cdna.artstation.com": "https://www.artstation.com/",
    "cdnb.artstation.com": "https://www.artstation.com/",
    "cdnc.artstation.com": "https://www.artstation.com/",
    "fandom.com": "https://www.fandom.com/",
    "steamstatic.com": "https://store.steampowered.com/",
    "googleusercontent.com": "https://play.google.com/",
}


@dataclass(frozen=True)
class ImageCandidate:
    url: str
    bucket: str
    tier: str
    source_label: str
    referer: str = ""
    caption: str = ""
    classification: str = "source_rule"

    def normalized_bucket(self) -> str:
        return self.bucket if self.bucket in BUCKETS else "unclassified"


@dataclass
class SourcingResult:
    selected: list[ImageCandidate]
    alternates: dict[str, list[ImageCandidate]]
    counts: dict[str, int]
    tiers_used: list[str]
    l3_triggered: bool
    quotas: dict[str, int]

    def all_candidates(self) -> list[ImageCandidate]:
        result = list(self.selected)
        for bucket in BUCKETS:
            result.extend(self.alternates.get(bucket, []))
        return result


@dataclass
class ProcessedImage:
    data: bytes
    width: int
    height: int
    format: str
    sha256: str
    perceptual_hash: int
    palette: list[dict]


class ImageRejected(ValueError):
    """Raised when an image is unusable for an evidence-backed report."""


class ImageDeduplicator:
    def __init__(self, max_hamming: int = 5):
        self.max_hamming = max_hamming
        self.byte_hashes: set[str] = set()
        self.perceptual_hashes: list[int] = []

    @staticmethod
    def _hamming(first: int, second: int) -> int:
        return bin(first ^ second).count("1")

    def add(self, image: ProcessedImage) -> bool:
        """Register an image and return True when it is a duplicate."""
        duplicate = image.sha256 in self.byte_hashes or any(
            self._hamming(image.perceptual_hash, existing) <= self.max_hamming
            for existing in self.perceptual_hashes
        )
        if not duplicate:
            self.byte_hashes.add(image.sha256)
            self.perceptual_hashes.append(image.perceptual_hash)
        return duplicate


def parse_steam_search(payload: dict, game_name: str) -> Optional[int]:
    items = payload.get("items") or []
    if not items:
        return None
    wanted = re.sub(r"\W+", "", game_name).casefold()
    exact = [
        item for item in items
        if re.sub(r"\W+", "", str(item.get("name", ""))).casefold() == wanted
    ]
    item = exact[0] if exact else items[0]
    try:
        return int(item["id"])
    except (KeyError, TypeError, ValueError):
        return None


def _balanced_store_bucket(index: int) -> str:
    # Store APIs do not label screenshot content. Rotate the provisional bucket
    # and require local visual review before prose analysis.
    return ("environment", "ui_gameplay", "character")[index % 3]


def parse_steam_appdetails(app_id: int, payload: dict) -> list[ImageCandidate]:
    wrapper = payload.get(str(app_id)) or payload.get(app_id) or {}
    if not wrapper.get("success"):
        return []
    data = wrapper.get("data") or {}
    result: list[ImageCandidate] = []
    header = data.get("header_image")
    if header:
        result.append(ImageCandidate(header, "key_art", "L1", "Steam"))
    for index, screenshot in enumerate(data.get("screenshots") or []):
        url = screenshot.get("path_full") or screenshot.get("path_thumbnail")
        if url:
            result.append(ImageCandidate(
                url,
                _balanced_store_bucket(index),
                "L1",
                "Steam",
                classification="quota_balance_requires_review",
            ))
    return result


def parse_itunes_search(payload: dict, game_name: str) -> list[ImageCandidate]:
    results = payload.get("results") or []
    if not results:
        return []
    wanted = game_name.casefold()
    matches = [item for item in results if wanted in str(item.get("trackName", "")).casefold()]
    item = matches[0] if matches else results[0]
    candidates: list[ImageCandidate] = []
    artwork = item.get("artworkUrl512") or item.get("artworkUrl100")
    if artwork:
        candidates.append(ImageCandidate(artwork, "key_art", "L1", "Apple App Store"))
    screenshots = list(item.get("screenshotUrls") or [])
    screenshots.extend(item.get("ipadScreenshotUrls") or [])
    seen: set[str] = set()
    for index, url in enumerate(screenshots):
        if not url or url in seen:
            continue
        seen.add(url)
        candidates.append(ImageCandidate(
            url,
            _balanced_store_bucket(index),
            "L1",
            "Apple App Store",
            classification="quota_balance_requires_review",
        ))
    return candidates


def parse_google_play_html(page_html: str) -> list[ImageCandidate]:
    decoded = html.unescape(page_html).replace("\\u003d", "=").replace("\\/", "/")
    urls = re.findall(
        r'https://play-lh\.googleusercontent\.com/[A-Za-z0-9_./?=:%&+\-]+',
        decoded,
    )
    candidates: list[ImageCandidate] = []
    seen: set[str] = set()
    for url in urls:
        url = url.rstrip('"\\')
        url = re.sub(r"=w\d+-h\d+(?:-[a-z0-9]+)*$", "=s0", url, flags=re.IGNORECASE)
        if url in seen:
            continue
        seen.add(url)
        bucket = "key_art" if not candidates else _balanced_store_bucket(len(candidates) - 1)
        candidates.append(ImageCandidate(
            url,
            bucket,
            "L1",
            "Google Play",
            referer="https://play.google.com/",
            classification="quota_balance_requires_review" if candidates else "source_rule",
        ))
    return candidates


def parse_artstation_project(payload: dict) -> list[ImageCandidate]:
    result: list[ImageCandidate] = []
    cover = payload.get("cover_url")
    if cover:
        result.append(ImageCandidate(
            cover, "key_art", "L2", "ArtStation", "https://www.artstation.com/"
        ))
    assets = payload.get("assets") or []
    for index, asset in enumerate(assets):
        url = asset.get("image_url") or asset.get("url")
        if not url:
            continue
        title = str(asset.get("title") or asset.get("name") or "").casefold()
        if any(word in title for word in ("character", "concept", "turnaround", "model")):
            bucket = "character"
            method = "metadata"
        elif any(word in title for word in ("ui", "hud", "menu", "interface")):
            bucket = "ui_gameplay"
            method = "metadata"
        else:
            bucket = _balanced_store_bucket(index)
            method = "quota_balance_requires_review"
        result.append(ImageCandidate(
            url,
            bucket,
            "L2",
            "ArtStation",
            referer="https://www.artstation.com/",
            caption=title,
            classification=method,
        ))
    return result


class _ImageHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        values = {key.casefold(): value for key, value in attrs if value}
        if tag.casefold() == "img":
            for key in ("data-src", "data-original", "src"):
                if values.get(key):
                    self.urls.append(values[key] or "")
                    break
            if values.get("srcset"):
                parts = [item.strip().split(" ")[0] for item in values["srcset"].split(",")]
                self.urls.extend(parts[-2:])
        if tag.casefold() == "meta":
            prop = (values.get("property") or values.get("name") or "").casefold()
            if prop in {"og:image", "twitter:image"} and values.get("content"):
                self.urls.append(values["content"] or "")


def parse_gallery_html(page_html: str, source_label: str, referer: str) -> list[ImageCandidate]:
    parser = _ImageHTMLParser()
    parser.feed(page_html)
    decoded = html.unescape(page_html).replace("\\/", "/")
    json_urls = re.findall(
        r'"(?:image_url|download_url|full|original|url)"\s*:\s*"(https?://[^" ]+\.(?:jpe?g|png|webp|avif)(?:\?[^" ]*)?)"',
        decoded,
        flags=re.IGNORECASE,
    )
    result: list[ImageCandidate] = []
    seen: set[str] = set()
    for index, raw_url in enumerate(parser.urls + json_urls):
        url = html.unescape(raw_url).replace("\\u0026", "&")
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        result.append(ImageCandidate(
            url,
            _balanced_store_bucket(index),
            "L2",
            source_label,
            referer=referer,
            classification="quota_balance_requires_review",
        ))
    return result


def _dedupe_candidates(candidates: Iterable[ImageCandidate]) -> list[ImageCandidate]:
    seen: set[str] = set()
    result: list[ImageCandidate] = []
    for candidate in candidates:
        if not candidate.url or candidate.url in seen:
            continue
        seen.add(candidate.url)
        result.append(candidate)
    return result


def _add_candidates(
    selected: list[ImageCandidate],
    alternates: dict[str, list[ImageCandidate]],
    candidates: Iterable[ImageCandidate],
    quotas: dict[str, int],
) -> None:
    selected_urls = {item.url for item in selected}
    alternate_urls = {item.url for values in alternates.values() for item in values}
    counts = Counter(item.normalized_bucket() for item in selected)
    for candidate in _dedupe_candidates(candidates):
        if candidate.url in selected_urls or candidate.url in alternate_urls:
            continue
        bucket = candidate.normalized_bucket()
        if bucket == "unclassified":
            bucket = max(BUCKETS, key=lambda key: quotas.get(key, 0) - counts[key])
            candidate = ImageCandidate(
                candidate.url, bucket, candidate.tier, candidate.source_label,
                candidate.referer, candidate.caption, "quota_balance_requires_review",
            )
        if counts[bucket] < quotas.get(bucket, 0):
            selected.append(candidate)
            selected_urls.add(candidate.url)
            counts[bucket] += 1
        else:
            alternates.setdefault(bucket, []).append(candidate)
            alternate_urls.add(candidate.url)


def _quotas_met(selected: Iterable[ImageCandidate], quotas: dict[str, int]) -> bool:
    counts = Counter(item.normalized_bucket() for item in selected)
    return all(counts[bucket] >= quotas.get(bucket, 0) for bucket in BUCKETS)


def _needs_l3(selected: Iterable[ImageCandidate], min_total: int) -> bool:
    selected = list(selected)
    counts = Counter(item.normalized_bucket() for item in selected)
    return len(selected) < min_total or any(counts[bucket] == 0 for bucket in CORE_BUCKETS)


def run_tiered_collection(
    quotas: dict[str, int],
    l1_provider: Callable[[], list[ImageCandidate]],
    l2_provider: Callable[[], list[ImageCandidate]],
    l3_provider: Callable[[], list[ImageCandidate]],
    min_total: int = DEFAULT_MIN_TOTAL,
) -> SourcingResult:
    quotas = {bucket: max(0, int(quotas.get(bucket, 0))) for bucket in BUCKETS}
    selected: list[ImageCandidate] = []
    alternates = {bucket: [] for bucket in BUCKETS}
    tiers_used: list[str] = []

    _add_candidates(selected, alternates, l1_provider(), quotas)
    tiers_used.append("L1")
    if not _quotas_met(selected, quotas):
        _add_candidates(selected, alternates, l2_provider(), quotas)
        tiers_used.append("L2")
    l3_triggered = _needs_l3(selected, min_total)
    if l3_triggered:
        _add_candidates(selected, alternates, l3_provider(), quotas)
        tiers_used.append("L3")

    counts = {bucket: 0 for bucket in BUCKETS}
    counts.update(Counter(item.normalized_bucket() for item in selected))
    return SourcingResult(selected, alternates, counts, tiers_used, l3_triggered, quotas)


def _perceptual_hash(image: Image.Image) -> int:
    grayscale = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(grayscale.getdata())
    value = 0
    for row in range(8):
        for column in range(8):
            value = (value << 1) | int(
                pixels[row * 9 + column] > pixels[row * 9 + column + 1]
            )
    return value


def extract_hex_palette(image: Image.Image, colors: int = 5) -> list[dict]:
    sample = image.convert("RGB")
    sample.thumbnail((256, 256), Image.Resampling.LANCZOS)
    usable = []
    for red, green, blue in sample.getdata():
        luminance = (red * 299 + green * 587 + blue * 114) / 1000
        if luminance <= 24 or luminance >= 235:
            continue
        usable.append((red, green, blue))
    if not usable:
        return []
    strip = Image.new("RGB", (len(usable), 1))
    strip.putdata(usable)
    quantized = strip.quantize(colors=max(1, colors), method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette() or []
    counts = Counter(quantized.getdata())
    total = sum(counts.values()) or 1
    result = []
    for index, count in counts.most_common(colors):
        offset = index * 3
        rgb = tuple(palette[offset:offset + 3])
        if len(rgb) != 3:
            continue
        result.append({
            "hex": "#{:02X}{:02X}{:02X}".format(*rgb),
            "ratio": round(count / total, 4),
        })
    return result


def normalize_for_feishu_bytes(
    raw: bytes,
    min_width: int = 480,
    min_height: int = 270,
    max_dimension: int = 4096,
    max_bytes: int = 8 * 1024 * 1024,
    quality: int = 88,
) -> ProcessedImage:
    try:
        image = Image.open(BytesIO(raw))
        image.load()
        image = ImageOps.exif_transpose(image)
    except Exception as error:
        raise ImageRejected(f"decode_failed: {error}") from error
    width, height = image.size
    if width < min_width or height < min_height:
        raise ImageRejected(f"low_resolution: {width}x{height}")
    aspect_ratio = width / max(1, height)
    if aspect_ratio > 4.0 or aspect_ratio < 0.25:
        raise ImageRejected(f"banner_or_extreme_aspect: {width}x{height}")

    quality_sample = image.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR)
    variance = sum(ImageStat.Stat(quality_sample).var) / 3
    if variance < 6 or quality_sample.entropy() < 1.2:
        raise ImageRejected("pure_color_or_corrupt")

    if max(image.size) > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

    has_alpha = image.mode in {"RGBA", "LA"} and image.getchannel("A").getextrema()[0] < 255
    output_format = "PNG" if has_alpha else "JPEG"
    if output_format == "PNG":
        normalized = image.convert("RGBA")
    else:
        if image.mode in {"RGBA", "LA"}:
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            normalized = background
        else:
            normalized = image.convert("RGB")

    def encode(current: Image.Image, jpeg_quality: int) -> bytes:
        buffer = BytesIO()
        if output_format == "PNG":
            current.save(buffer, format="PNG", optimize=True)
        else:
            current.save(
                buffer,
                format="JPEG",
                quality=jpeg_quality,
                optimize=True,
                progressive=True,
            )
        return buffer.getvalue()

    encoded = encode(normalized, quality)
    current_quality = quality
    while len(encoded) > max_bytes and current_quality > 62 and output_format == "JPEG":
        current_quality -= 8
        encoded = encode(normalized, current_quality)
    while len(encoded) > max_bytes and min(normalized.size) > min(min_width, min_height):
        normalized = normalized.resize(
            (max(1, int(normalized.width * 0.85)), max(1, int(normalized.height * 0.85))),
            Image.Resampling.LANCZOS,
        )
        encoded = encode(normalized, current_quality)
    if len(encoded) > max_bytes:
        raise ImageRejected(f"too_large_after_compression: {len(encoded)}")

    return ProcessedImage(
        data=encoded,
        width=normalized.width,
        height=normalized.height,
        format=output_format,
        sha256=hashlib.sha256(encoded).hexdigest(),
        perceptual_hash=_perceptual_hash(normalized),
        palette=extract_hex_palette(normalized),
    )


def referer_for_url(url: str, explicit: str = "") -> str:
    if explicit:
        return explicit
    domain = (urlparse(url).hostname or "").casefold()
    for suffix, referer in DOMAIN_REFERERS.items():
        if domain == suffix or domain.endswith("." + suffix):
            return referer
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/" if parsed.scheme and parsed.netloc else ""


def download_image(candidate: ImageCandidate, timeout: int = 20) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    referer = referer_for_url(candidate.url, candidate.referer)
    if referer:
        headers["Referer"] = referer
    last_error: Optional[Exception] = None
    urls = artstation_fallback_urls(candidate.url)
    for url in urls:
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.content
        except Exception as error:
            last_error = error
    raise RuntimeError(f"download_failed: {last_error}")


def artstation_fallback_urls(url: str) -> list[str]:
    parsed = urlparse(url)
    domain = (parsed.hostname or "").casefold()
    candidates = [url]
    if "artstation" not in domain:
        return candidates
    path = parsed.path
    if "/images/images/" in path:
        candidates.append(parsed._replace(path=path.replace("/images/images/", "/covers/images/")).geturl())
    if "/large/" in path:
        for size in ("4k", "medium", "small"):
            candidates.append(parsed._replace(path=path.replace("/large/", f"/{size}/")).geturl())
    for source, destination in (("cdna", "cdnb"), ("cdnb", "cdnc"), ("cdnc", "cdna")):
        if domain.startswith(source + "."):
            candidates.append(parsed._replace(netloc=parsed.netloc.replace(source, destination, 1)).geturl())
            break
    if parsed.query:
        candidates.append(parsed._replace(query="").geturl())
    return list(dict.fromkeys(candidates))


def collect_steam(game_name: str) -> list[ImageCandidate]:
    search = requests.get(
        "https://store.steampowered.com/api/storesearch/",
        params={"term": game_name, "l": "english", "cc": "US"},
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    search.raise_for_status()
    app_id = parse_steam_search(search.json(), game_name)
    if app_id is None:
        return []
    details = requests.get(
        "https://store.steampowered.com/api/appdetails",
        params={"appids": app_id, "l": "english", "cc": "US"},
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    details.raise_for_status()
    return parse_steam_appdetails(app_id, details.json())


def collect_itunes(game_name: str, country: str = "us") -> list[ImageCandidate]:
    response = requests.get(
        "https://itunes.apple.com/search",
        params={"term": game_name, "entity": "software", "country": country, "limit": 10},
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    response.raise_for_status()
    return parse_itunes_search(response.json(), game_name)


def collect_google_play(url: str) -> list[ImageCandidate]:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Referer": "https://play.google.com/"},
        timeout=20,
    )
    response.raise_for_status()
    return parse_google_play_html(response.text)


def collect_l2_pages(urls: Iterable[str]) -> list[ImageCandidate]:
    candidates: list[ImageCandidate] = []
    for url in urls:
        try:
            parsed = urlparse(url)
            domain = parsed.hostname or ""
            if "artstation.com" in domain:
                match = re.search(r"/artwork/([A-Za-z0-9_-]+)", parsed.path)
                if match:
                    api_url = f"https://www.artstation.com/projects/{match.group(1)}.json"
                    response = requests.get(
                        api_url,
                        headers={"User-Agent": USER_AGENT, "Referer": url},
                        timeout=20,
                    )
                    if response.ok:
                        candidates.extend(parse_artstation_project(response.json()))
                        continue
            response = requests.get(
                url,
                headers={"User-Agent": USER_AGENT, "Referer": referer_for_url(url)},
                timeout=20,
            )
            response.raise_for_status()
            label = "Fandom Wiki" if "fandom.com" in domain else "Official / Press Kit"
            candidates.extend(parse_gallery_html(response.text, label, url))
        except Exception as error:
            log.warning("L2 page failed [%s]: %s", url, error)
    return _dedupe_candidates(candidates)


def locate_video_tool(name: str) -> Optional[str]:
    env_name = "GAME_ART_YTDLP" if name == "yt-dlp" else "GAME_ART_FFMPEG"
    configured = os.environ.get(env_name)
    candidates = [
        configured,
        shutil.which(name),
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def extract_video_candidates(
    game_name: str,
    video_urls: Iterable[str],
    output_dir: Path,
) -> list[ImageCandidate]:
    ytdlp = locate_video_tool("yt-dlp")
    ffmpeg = locate_video_tool("ffmpeg")
    if not ytdlp or not ffmpeg:
        missing = [name for name, path in (("yt-dlp", ytdlp), ("ffmpeg", ffmpeg)) if not path]
        log.warning("L3 skipped; missing: %s", ", ".join(missing))
        return []
    output_dir.mkdir(parents=True, exist_ok=True)
    targets = list(video_urls) or [f"ytsearch3:{game_name} official gameplay trailer"]
    candidates: list[ImageCandidate] = []
    with tempfile.TemporaryDirectory(prefix=f"game-art-video-{os.getpid()}-") as temp_dir:
        template = str(Path(temp_dir) / "source.%(ext)s")
        command = [
            ytdlp,
            "--no-playlist",
            "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
            "--print", "after_move:filepath",
            "--print", "duration",
            "-o", template,
            targets[0],
        ]
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            log.warning("L3 video download failed: %s", result.stderr.strip()[-500:])
            return []
        paths = [Path(line.strip()) for line in result.stdout.splitlines() if Path(line.strip()).is_file()]
        durations = []
        for line in result.stdout.splitlines():
            try:
                durations.append(float(line.strip()))
            except ValueError:
                continue
        if not paths:
            paths = list(Path(temp_dir).glob("source.*"))
        if not paths:
            return []
        frame_pattern = str(output_dir / "video_%05d.jpg")
        extraction_command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "3",
            "-i", str(paths[-1]),
        ]
        if durations and durations[-1] > 8:
            extraction_command.extend(["-t", str(max(1.0, durations[-1] - 6.0))])
        extraction_command.extend([
            "-vf", "fps=1/5,scale='min(1920,iw)':-2",
            "-q:v", "2", frame_pattern,
        ])
        extraction = subprocess.run(
            extraction_command,
            check=False,
            capture_output=True,
            text=True,
        )
        if extraction.returncode != 0:
            log.warning("L3 frame extraction failed: %s", extraction.stderr.strip()[-500:])
            return []
    frames = sorted(output_dir.glob("video_*.jpg"))
    for index, frame in enumerate(frames):
        try:
            normalized = normalize_for_feishu_bytes(frame.read_bytes())
        except ImageRejected:
            continue
        # Logo/title cards are typically low entropy/variance and have already
        # been rejected. Remaining frames rotate through the evidence buckets;
        # SKILL.md requires the model to visually review them before analysis.
        bucket = CORE_BUCKETS[index % len(CORE_BUCKETS)]
        frame.write_bytes(normalized.data)
        candidates.append(ImageCandidate(
            str(frame),
            bucket,
            "L3",
            "视频抽帧",
            classification="quota_balance_requires_review",
        ))
    return candidates


def materialize_candidates(
    result: SourcingResult,
    output_dir: Path,
    downloader: Callable[[ImageCandidate], bytes] = download_image,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    deduper = ImageDeduplicator()
    selected_records = []
    rejected = []
    all_by_bucket = {bucket: [] for bucket in BUCKETS}
    for candidate in result.selected:
        all_by_bucket[candidate.normalized_bucket()].append(candidate)
    for bucket, values in result.alternates.items():
        all_by_bucket[bucket].extend(values)

    for bucket in BUCKETS:
        accepted: list[tuple[ImageCandidate, ProcessedImage, Path]] = []
        for candidate in _dedupe_candidates(all_by_bucket[bucket]):
            try:
                raw = Path(candidate.url).read_bytes() if Path(candidate.url).is_file() else downloader(candidate)
                processed = normalize_for_feishu_bytes(raw)
                if deduper.add(processed):
                    raise ImageRejected("duplicate")
                extension = ".png" if processed.format == "PNG" else ".jpg"
                local_path = output_dir / f"{bucket}-{len(accepted) + 1:02d}-{processed.sha256[:10]}{extension}"
                local_path.write_bytes(processed.data)
                accepted.append((candidate, processed, local_path))
            except Exception as error:
                rejected.append({
                    "bucket": bucket,
                    "url": candidate.url,
                    "tier": candidate.tier,
                    "reason": str(error).splitlines()[0][:240],
                })
            if len(accepted) >= result.quotas.get(bucket, 0) + 1:
                break
        primary_count = min(len(accepted), result.quotas.get(bucket, 0))
        alternate_urls = [str(item[2]) for item in accepted[primary_count:]]
        for candidate, processed, local_path in accepted[:primary_count]:
            selected_records.append({
                "bucket": bucket,
                "local_path": str(local_path),
                "source_url": candidate.url,
                "source_tier": candidate.tier,
                "source_label": candidate.source_label,
                "classification": candidate.classification,
                "width": processed.width,
                "height": processed.height,
                "sha256": processed.sha256,
                "perceptual_hash": f"{processed.perceptual_hash:016x}",
                "palette": processed.palette,
                "alternates": list(alternate_urls),
            })

    counts = {bucket: 0 for bucket in BUCKETS}
    counts.update(Counter(item["bucket"] for item in selected_records))
    palettes = {
        bucket: [
            {"image": item["local_path"], "colors": item["palette"]}
            for item in selected_records
            if item["bucket"] == bucket and item["palette"]
        ][:2]
        for bucket in ("character", "environment")
    }
    report_blocks = [
        {
            "type": "image",
            "url": item["local_path"],
            "bucket": item["bucket"],
            "source_tier": item["source_tier"],
            "source_label": item["source_label"],
            "alternates": [
                {"url": path, "bucket": item["bucket"]}
                for path in item["alternates"]
            ],
            "caption": f"{item['bucket']} evidence — visual review required",
        }
        for item in selected_records
    ]
    return {
        "images": selected_records,
        "report_blocks": report_blocks,
        "counts": counts,
        "quotas": result.quotas,
        "tiers_used": result.tiers_used,
        "l3_triggered": result.l3_triggered,
        "palettes": palettes,
        "rejected": rejected,
        "quality_gate": {
            "min_width": 480,
            "min_height": 270,
            "max_bytes": 8 * 1024 * 1024,
            "dedupe": "sha256+dhash",
        },
    }


def parse_quotas(values: Iterable[str]) -> dict[str, int]:
    quotas = dict(DEFAULT_QUOTAS)
    for value in values:
        if "=" not in value:
            raise ValueError(f"Invalid quota '{value}', expected bucket=number")
        bucket, raw_count = value.split("=", 1)
        if bucket not in BUCKETS:
            raise ValueError(f"Unknown bucket '{bucket}'")
        quotas[bucket] = max(0, int(raw_count))
    if sum(quotas.values()) > 14:
        raise ValueError("Total target quota must be <= 14")
    return quotas


def source_game(
    game_name: str,
    platform: str,
    page_urls: list[str],
    google_play_url: str,
    video_urls: list[str],
    quotas: dict[str, int],
    min_total: int,
    output_dir: Path,
    country: str = "us",
) -> dict:
    def l1() -> list[ImageCandidate]:
        result: list[ImageCandidate] = []
        if platform in {"auto", "steam"}:
            try:
                result.extend(collect_steam(game_name))
            except Exception as error:
                log.warning("Steam L1 failed: %s", error)
        if platform in {"auto", "mobile"}:
            try:
                result.extend(collect_itunes(game_name, country=country))
            except Exception as error:
                log.warning("iTunes L1 failed: %s", error)
            if google_play_url:
                try:
                    result.extend(collect_google_play(google_play_url))
                except Exception as error:
                    log.warning("Google Play L1 failed: %s", error)
        return _dedupe_candidates(result)

    def l2() -> list[ImageCandidate]:
        try:
            return collect_l2_pages(page_urls)
        except Exception as error:
            log.warning("L2 page collection failed: %s", error)
            return []

    video_dir = output_dir / "video-frames"

    def l3() -> list[ImageCandidate]:
        return extract_video_candidates(game_name, video_urls, video_dir)

    result = run_tiered_collection(quotas, l1, l2, l3, min_total=min_total)
    manifest = materialize_candidates(result, output_dir / "images")
    # A source may look plentiful before downloads but fail local QC. Escalate to
    # video once more when the *usable* evidence still violates the hard gate.
    usable_counts = manifest["counts"]
    if (
        not result.l3_triggered
        and (sum(usable_counts.values()) < min_total or any(usable_counts[bucket] == 0 for bucket in CORE_BUCKETS))
    ):
        video_candidates = l3()
        if video_candidates:
            _add_candidates(result.selected, result.alternates, video_candidates, quotas)
            result.l3_triggered = True
            if "L3" not in result.tiers_used:
                result.tiers_used.append("L3")
            manifest = materialize_candidates(result, output_dir / "images")
    manifest.update({
        "game": game_name,
        "platform": platform,
        "generated_at": int(time.time()),
        "report_image_data_version": 1,
    })
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect and quality-gate game art images")
    parser.add_argument("game_name")
    parser.add_argument("--platform", choices=("auto", "steam", "mobile"), default="auto")
    parser.add_argument("--page-url", action="append", default=[], help="Official/press kit/ArtStation/Fandom page")
    parser.add_argument("--google-play-url", default="")
    parser.add_argument("--video-url", action="append", default=[])
    parser.add_argument("--quota", action="append", default=[], help="bucket=count; total <= 14")
    parser.add_argument("--min-total", type=int, default=DEFAULT_MIN_TOTAL)
    parser.add_argument("--country", default="us")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()

    quotas = parse_quotas(args.quota)
    safe_slug = re.sub(r"[^a-z0-9]+", "-", args.game_name.casefold()).strip("-") or "game"
    output_dir = args.output_dir or Path(f"/tmp/game-art-{safe_slug}-sourcing-{os.getpid()}")
    manifest_path = args.manifest or output_dir / "image-manifest.json"
    manifest = source_game(
        args.game_name,
        args.platform,
        args.page_url,
        args.google_play_url,
        args.video_url,
        quotas,
        max(1, args.min_total),
        output_dir,
        country=args.country,
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    counts = manifest["counts"]
    print(str(manifest_path))
    print(
        "入选 {total} 张（角色 {character} / 场景 {environment} / UI {ui_gameplay} / 主视觉 {key_art}）".format(
            total=sum(counts.values()), **counts
        )
    )
    print("来源层级：" + " + ".join(manifest["tiers_used"]))
    print(f"剔除：{len(manifest['rejected'])} 张")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
