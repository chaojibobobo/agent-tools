"""Validate or publish a game-art JSON report to Feishu Docs.

Usage:
    python3 publish_to_feishu.py report.json --validate-only
    python3 publish_to_feishu.py report.json --title "标题"
    python3 publish_to_feishu.py report.json --grant-user-access

Credentials default to ~/.config/game-art-sourcing/config.yaml. Override with
--config or GAME_ART_CONFIG. Validation is local and never loads credentials.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT))
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from block_builder import json_to_blocks_with_metadata  # noqa: E402
from feishu_client import FeishuClient  # noqa: E402
from image_sourcing import (  # noqa: E402
    CORE_BUCKETS,
    ImageDeduplicator,
    normalize_for_feishu_bytes,
)


logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("feishu-publish")

_HOTLINK_DOMAINS = {
    "artstation.com": "https://www.artstation.com",
    "cdna.artstation.com": "https://www.artstation.com",
    "cdnb.artstation.com": "https://www.artstation.com",
    "cdnc.artstation.com": "https://www.artstation.com",
}
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)


def _default_config_path() -> Path:
    configured = os.environ.get("GAME_ART_CONFIG")
    if configured:
        return Path(configured).expanduser()
    config_base = Path(
        os.environ.get("XDG_CONFIG_HOME", "~/.config")
    ).expanduser()
    return config_base / "game-art-sourcing" / "config.yaml"


def _fix_steam_url(url: str) -> str:
    """Convert the old Steam screenshot URL shape to the current CDN shape."""
    import re

    match = re.match(
        r"(https?://[^/]+/store_item_assets/steam/apps/\d+)/ss_([a-f0-9]+)\.jpg$",
        url,
    )
    if not match:
        return url
    prefix, hash_value = match.group(1), match.group(2)
    return (
        f"{prefix}/{hash_value}/ss_{hash_value}.1920x1080.jpg"
        f"?t={int(time.time())}"
    )


def _download(url: str, timeout: int = 15) -> requests.Response:
    """Download an image with bounded ArtStation CDN fallbacks."""
    url = _fix_steam_url(url)
    parsed = urlparse(url)
    domain = parsed.hostname or ""
    headers = {"User-Agent": _UA}
    for blocked_domain, referer in _HOTLINK_DOMAINS.items():
        if domain == blocked_domain or domain.endswith("." + blocked_domain):
            headers["Referer"] = referer
            break

    response = requests.get(url, timeout=timeout, headers=headers)
    if response.status_code != 403 or "artstation" not in domain:
        return response

    path = parsed.path
    candidates = []
    if "/images/images/" in path:
        candidates.append(
            parsed._replace(path=path.replace("/images/images/", "/covers/images/"))
        )
    for source, destination in [
        ("cdna", "cdnb"),
        ("cdnb", "cdnc"),
        ("cdnc", "cdna"),
    ]:
        if domain.startswith(source + "."):
            candidates.append(
                parsed._replace(netloc=domain.replace(source, destination, 1))
            )
            break
    if "/large/" in path:
        for size in ("/medium/", "/small/", "/4k/"):
            candidates.append(parsed._replace(path=path.replace("/large/", size)))
    if parsed.query:
        candidates.append(parsed._replace(query=""))

    for candidate in candidates:
        fallback = requests.get(
            candidate.geturl(),
            timeout=timeout,
            headers=headers,
        )
        if fallback.ok:
            return fallback
    return response


def _load_image_source(source: str, timeout: int = 15) -> bytes:
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https"):
        response = _download(source, timeout=timeout)
        response.raise_for_status()
        return response.content
    path = unquote(parsed.path) if parsed.scheme == "file" else source
    return Path(path).expanduser().read_bytes()


def _compress_image(
    data: bytes,
    max_width: int = 1200,
    quality: int = 85,
) -> tuple[bytes, int, int]:
    processed = normalize_for_feishu_bytes(
        data,
        max_dimension=max_width,
        quality=quality,
    )
    return processed.data, processed.width, processed.height


def _get_status_file(input_file: str) -> Path:
    base = Path(input_file).stem
    return Path("/tmp") / f"{base}-publish-status.json"


def _write_status(status_file: Path, stage: str, **details: object) -> None:
    payload = {"stage": stage, "ts": time.time(), **details}
    status_file.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_config(config_path: Path) -> dict:
    if not config_path.is_file():
        raise FileNotFoundError(
            f"Config not found: {config_path}. "
            "Copy config.example.yaml there and fill in Feishu credentials."
        )
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    feishu = config.get("feishu") or {}
    missing = [
        key for key in ("app_id", "app_secret")
        if not feishu.get(key)
    ]
    if missing:
        raise ValueError(
            "Missing Feishu config fields: " + ", ".join(missing)
        )
    return feishu


def _load_report(
    input_file: Path,
) -> tuple[dict, list[dict], dict[int, str], dict[int, dict]]:
    data = json.loads(input_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Report root must be a JSON object")
    blocks, image_map, image_meta = json_to_blocks_with_metadata(data)
    if not blocks:
        raise ValueError("Report contains no publishable blocks")
    return data, blocks, image_map, image_meta


@dataclass
class PreparedImages:
    data: dict[int, bytes]
    alternates: dict[int, list[bytes]]
    sizes: dict[int, tuple[int, int]]
    image_meta: dict[int, dict]
    failures: list[dict]
    palettes: dict[int, list[dict]]


def _download_images(
    image_map: dict[int, str],
) -> tuple[dict[int, bytes], dict[int, tuple[int, int]]]:
    """Compatibility wrapper retained for callers outside this package."""
    prepared = _prepare_images(image_map, {index: {} for index in image_map})
    return prepared.data, prepared.sizes


def _candidate_sources(primary: str, meta: dict) -> list[dict]:
    bucket = meta.get("bucket", "unclassified")
    result = [{
        "url": primary,
        "bucket": bucket,
        "source_tier": meta.get("source_tier", "unknown"),
        "source_label": meta.get("source_label", "unknown"),
    }]
    for alternate in meta.get("alternates") or []:
        candidate = alternate if isinstance(alternate, dict) else {"url": alternate}
        if not candidate.get("url"):
            continue
        if candidate.get("bucket", bucket) != bucket:
            continue
        normalized = dict(candidate)
        normalized.setdefault("bucket", bucket)
        normalized.setdefault("source_tier", meta.get("source_tier", "unknown"))
        normalized.setdefault("source_label", meta.get("source_label", "unknown"))
        result.append(normalized)
    return result


def _prepare_images(
    image_map: dict[int, str],
    image_meta: dict[int, dict],
) -> PreparedImages:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    image_data: dict[int, bytes] = {}
    image_alternates: dict[int, list[bytes]] = {}
    image_sizes: dict[int, tuple[int, int]] = {}
    palettes: dict[int, list[dict]] = {}
    selected_meta = {index: dict(image_meta.get(index, {})) for index in image_map}
    failures: list[dict] = []
    deduper = ImageDeduplicator()
    slot_results: dict[int, tuple[list[tuple[bytes, object, dict]], list[dict]]] = {}

    def load_slot(index: int, source: str) -> tuple[int, list[tuple[bytes, object, dict]], list[dict]]:
        accepted = []
        rejected = []
        for candidate_position, candidate in enumerate(_candidate_sources(source, selected_meta[index])):
            try:
                raw = _load_image_source(candidate["url"], timeout=20)
                processed = normalize_for_feishu_bytes(raw)
                accepted.append((processed.data, processed, candidate))
            except Exception as error:
                rejected.append({
                    "index": index,
                    "bucket": selected_meta[index].get("bucket", "unclassified"),
                    "reason": str(error).splitlines()[0][:240],
                    "candidate": candidate_position + 1,
                })
        return index, accepted, rejected

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(load_slot, index, source): index
            for index, source in image_map.items()
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                _, accepted, rejected = future.result()
                slot_results[index] = (accepted, rejected)
            except Exception as download_error:
                slot_results[index] = ([], [{
                    "index": index,
                    "bucket": selected_meta[index].get("bucket", "unclassified"),
                    "reason": str(download_error).splitlines()[0][:240],
                }])
                log.warning("Image preparation failed [%s]: %s", image_map[index], download_error)

    # Download concurrently, but perform global perceptual dedupe in stable
    # report order so repeated runs choose the same primary/backup images.
    for index in sorted(image_map):
        accepted, rejected = slot_results.get(index, ([], []))
        failures.extend(rejected)
        usable = []
        for data, processed, candidate in accepted:
            if deduper.add(processed):
                failures.append({
                    "index": index,
                    "bucket": selected_meta[index].get("bucket", "unclassified"),
                    "reason": "duplicate",
                })
                continue
            usable.append((data, processed, candidate))
            if len(usable) >= 2:
                break
        if not usable:
            failures.append({
                "index": index,
                "bucket": selected_meta[index].get("bucket", "unclassified"),
                "reason": "no_usable_candidate",
            })
            continue
        primary_data, primary, candidate = usable[0]
        image_data[index] = primary_data
        image_sizes[index] = (primary.width, primary.height)
        palettes[index] = primary.palette
        selected_meta[index].update({
            "source_tier": candidate.get("source_tier", "unknown"),
            "source_label": candidate.get("source_label", "unknown"),
            "selected_source": candidate.get("url", image_map[index]),
        })
        if candidate.get("url") != image_map[index]:
            selected_meta[index]["download_replacement"] = True
        image_alternates[index] = [item[0] for item in usable[1:]]
    return PreparedImages(
        image_data,
        image_alternates,
        image_sizes,
        selected_meta,
        failures,
        palettes,
    )


def _compact_prepared_blocks(
    blocks: list[dict],
    prepared: PreparedImages,
) -> tuple[list[dict], dict[int, bytes], dict[int, list[bytes]], dict[int, dict]]:
    failed_indices = {
        index for index, block in enumerate(blocks)
        if block.get("block_type") == 27 and index not in prepared.data
    }
    failed_captions = {
        prepared.image_meta[index].get("caption_index")
        for index in failed_indices
        if index in prepared.image_meta
    }
    skip = failed_indices | {index for index in failed_captions if isinstance(index, int)}
    compact_blocks = []
    compact_data = {}
    compact_alternates = {}
    compact_meta = {}
    for old_index, block in enumerate(blocks):
        if old_index in skip:
            continue
        new_index = len(compact_blocks)
        compact_blocks.append(block)
        if old_index in prepared.data:
            compact_data[new_index] = prepared.data[old_index]
            compact_alternates[new_index] = prepared.alternates.get(old_index, [])
            compact_meta[new_index] = dict(prepared.image_meta.get(old_index, {}))
            compact_meta[new_index]["original_index"] = old_index
    return compact_blocks, compact_data, compact_alternates, compact_meta


def build_image_receipt(
    requested: int,
    successful_indices: set[int],
    image_meta: dict[int, dict],
    failures: list[dict],
    actual_count: int | None = None,
) -> dict:
    bucket_counts = {bucket: 0 for bucket in ("key_art", *CORE_BUCKETS)}
    sources = []
    for index in sorted(successful_indices):
        meta = image_meta.get(index, {})
        bucket = meta.get("bucket", "unclassified")
        if bucket in bucket_counts:
            bucket_counts[bucket] += 1
        source = meta.get("source_label")
        if source and source != "unknown" and source not in sources:
            sources.append(source)
    effective_count = len(successful_indices) if actual_count is None else actual_count
    missing = [bucket for bucket in CORE_BUCKETS if bucket_counts[bucket] == 0]
    reasons = Counter(item.get("reason", "unknown") for item in failures)
    rejected_count = max(requested - effective_count, len(failures))
    if sum(reasons.values()) < rejected_count:
        reasons["not_written"] += rejected_count - sum(reasons.values())
    return {
        "requested": requested,
        "written": effective_count,
        "bucket_counts": bucket_counts,
        "sources": sources,
        "missing_buckets": missing,
        "incomplete": effective_count < 6 or bool(missing),
        "rejected_failed": rejected_count,
        "failure_reasons": dict(reasons),
    }


def build_incomplete_notice(receipt: dict) -> str:
    names = {
        "character": "角色",
        "environment": "场景",
        "ui_gameplay": "UI/实机",
    }
    missing = "、".join(names.get(item, item) for item in receipt["missing_buckets"])
    if not missing:
        missing = "总量未达 6 张"
    return f"【图源残缺：总计 {receipt['written']} 张，缺失板块：{missing}】"


def _incomplete_notice_block(text: str) -> dict:
    return {
        "block_type": 2,
        "text": {
            "elements": [{
                "text_run": {
                    "content": "⚠️ " + text,
                    "text_element_style": {"bold": True, "text_color": 1},
                }
            }],
            "style": {},
        },
    }


def format_publish_receipt(url: str, receipt: dict) -> str:
    counts = receipt["bucket_counts"]
    sources = " + ".join(receipt["sources"]) or "未标注"
    reason_text = " / ".join(
        f"{reason} {count}" for reason, count in sorted(receipt["failure_reasons"].items())
    ) or "无"
    gate = "｜图源残缺" if receipt["incomplete"] else "｜门禁通过"
    return "\n".join([
        url,
        (
            f"入图 {receipt['written']}/{receipt['requested']}"
            f"（角色 {counts['character']} / 场景 {counts['environment']} / "
            f"UI {counts['ui_gameplay']} / 主视觉 {counts['key_art']}）"
            f"｜来源：{sources}"
        ),
        f"剔除/失败：{receipt['rejected_failed']} 张（{reason_text}）{gate}",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate or publish a game-art JSON report"
    )
    parser.add_argument("input_file", help="JSON report path")
    parser.add_argument("--title", help="Feishu document title")
    parser.add_argument(
        "--config",
        type=Path,
        help="Config path; defaults to GAME_ART_CONFIG or XDG config",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Convert locally without credentials, network, or document writes",
    )
    parser.add_argument(
        "--grant-user-access",
        action="store_true",
        help="Explicitly grant configured user_open_id full_access",
    )
    args = parser.parse_args()

    input_file = Path(args.input_file).expanduser().resolve()
    report, blocks, image_map, image_meta = _load_report(input_file)
    if args.validate_only:
        bucket_counts = Counter(
            meta.get("bucket", "unclassified") for meta in image_meta.values()
        )
        print(f"Validated: {len(blocks)} blocks, {len(image_map)} images")
        print(
            "Buckets: key_art={key_art}, character={character}, "
            "environment={environment}, ui_gameplay={ui_gameplay}, "
            "unclassified={unclassified}".format(
                **{key: bucket_counts.get(key, 0) for key in (
                    "key_art", "character", "environment", "ui_gameplay", "unclassified"
                )}
            )
        )
        return 0

    status_file = _get_status_file(str(input_file))
    _write_status(
        status_file,
        "converted",
        blocks=len(blocks),
        images=len(image_map),
    )

    config_path = (args.config or _default_config_path()).expanduser()
    config = _load_config(config_path)
    client = FeishuClient(
        app_id=config["app_id"],
        app_secret=config["app_secret"],
        folder_token=config.get("folder_token", ""),
        user_open_id=config.get("user_open_id", ""),
        doc_domain=config.get("doc_domain", "open.feishu.cn"),
        grant_user_access=args.grant_user_access,
    )

    _write_status(status_file, "preparing_images", images=len(image_map))
    prepared = _prepare_images(image_map, image_meta)
    blocks, image_data, image_alternates, compact_meta = _compact_prepared_blocks(
        blocks,
        prepared,
    )
    for index, (width, height) in {
        new_index: prepared.sizes[meta["original_index"]]
        for new_index, meta in compact_meta.items()
    }.items():
        if index < len(blocks) and blocks[index].get("block_type") == 27:
            blocks[index]["image"]["width"] = width
            blocks[index]["image"]["height"] = height

    title = args.title or report.get("title") or "Game Art Research Report"
    _write_status(
        status_file,
        "creating_doc",
        title=title,
        prepared_images=len(image_data),
        rejected=len(prepared.failures),
    )
    document_id = client.create_document(title)
    _write_status(status_file, "doc_created", doc_id=document_id)

    _write_status(status_file, "writing_blocks", doc_id=document_id)
    upload_receipt = client.create_blocks(
        document_id,
        blocks,
        image_data,
        image_alternates=image_alternates,
    )
    all_failures = list(prepared.failures) + list(upload_receipt.get("failures", []))
    actual_image_count = client.get_document_image_count(document_id)
    receipt = build_image_receipt(
        requested=len(image_map),
        successful_indices=set(upload_receipt.get("written_indices", [])),
        image_meta=compact_meta,
        failures=all_failures,
        actual_count=actual_image_count,
    )
    if receipt["incomplete"]:
        client.prepend_blocks(
            document_id,
            [_incomplete_notice_block(build_incomplete_notice(receipt))],
        )
    verified_image_count = client.get_document_image_count(document_id)
    if verified_image_count != receipt["written"]:
        raise RuntimeError(
            "Feishu image readback mismatch: "
            f"expected {receipt['written']}, got {verified_image_count}"
        )
    url = client.get_document_url(document_id)
    _write_status(
        status_file,
        "done",
        url=url,
        doc_id=document_id,
        receipt=receipt,
    )
    print(format_publish_receipt(url, receipt))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        log.error("Error: %s", error)
        raise SystemExit(2)
