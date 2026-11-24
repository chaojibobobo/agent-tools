"""Convert JSON report schema to Feishu Block API format.

Usage:
    from block_builder import json_to_blocks
    blocks, image_map = json_to_blocks(report_data)

JSON schema:
    {
      "title": "游戏名 — 美术调研报告",
      "blocks": [
        {"type": "heading", "level": 2, "elements": [{"text": "🎮 游戏画像"}]},
        {"type": "text", "elements": [{"text": "开发商", "bold": true}, {"text": " | ..."}]},
        {"type": "image", "url": "https://...", "caption": "图注"},
        {"type": "bullet", "elements": [...]},
        {"type": "ordered", "elements": [...]},
        {"type": "quote", "elements": [...]},
        {"type": "code", "text": "..."},
        {"type": "gallery", "urls": ["url1", "url2", ...]}
      ]
    }

Element (text run) properties:
    text (required): string content
    bold, italic, underline, strikethrough: boolean
    link: URL string
    inline_code: boolean
    color: int (1=pink 2=orange 3=yellow 4=green 5=blue 6=purple 7=gray)
"""
from __future__ import annotations

import json


def json_to_blocks(data: dict | str) -> tuple[list[dict], dict[int, str]]:
    """Convert JSON report to (blocks, image_map).

    Args:
        data: dict or JSON string with report structure.

    Returns:
        blocks: list of Feishu Block dicts.
        image_map: {block_index: image_url} for image blocks to be filled.
    """
    blocks, image_map, _ = json_to_blocks_with_metadata(data)
    return blocks, image_map


def json_to_blocks_with_metadata(
    data: dict | str,
) -> tuple[list[dict], dict[int, str], dict[int, dict]]:
    """Convert report JSON while preserving image bucket/fallback metadata.

    The original :func:`json_to_blocks` two-value contract remains unchanged.
    Publishers that need upload receipts and same-bucket fallbacks use this
    extended form.
    """
    if isinstance(data, str):
        data = json.loads(data)

    blocks: list[dict] = []
    image_map: dict[int, str] = {}
    image_meta: dict[int, dict] = {}

    for block_def in data.get("blocks", []):
        btype = block_def.get("type")

        if btype == "heading":
            level = min(block_def.get("level", 2), 9)
            elements = _build_elements(block_def.get("elements", []))
            if not elements:
                continue
            block_type = level + 2
            key = f"heading{level}"
            blocks.append({"block_type": block_type, key: {"elements": elements}})

        elif btype == "text":
            elements = _build_elements(block_def.get("elements", []))
            if not elements:
                continue
            style = {}
            align = block_def.get("align")
            if align:
                style["align"] = align
            blocks.append({"block_type": 2, "text": {"elements": elements, "style": style}})

        elif btype == "image":
            url = block_def.get("url", "")
            if not url:
                continue
            img_block = {"block_type": 27, "image": {}}
            if block_def.get("width"):
                img_block["image"]["width"] = block_def["width"]
            if block_def.get("height"):
                img_block["image"]["height"] = block_def["height"]
            if block_def.get("align"):
                img_block["image"]["alignment"] = block_def["align"]
            idx = len(blocks)
            blocks.append(img_block)
            image_map[idx] = url
            image_meta[idx] = _image_metadata(block_def)
            caption = block_def.get("caption", "")
            if caption:
                blocks.append(_quote_block(*_build_elements([{"text": caption}])))
                image_meta[idx]["caption_index"] = len(blocks) - 1

        elif btype == "gallery":
            gallery_width = block_def.get("width")
            gallery_height = block_def.get("height")
            items = block_def.get("items") or block_def.get("urls", [])
            for item in items:
                item_def = item if isinstance(item, dict) else {"url": item}
                url = item_def.get("url", "")
                if not url:
                    continue
                img_block = {"block_type": 27, "image": {}}
                if gallery_width:
                    img_block["image"]["width"] = gallery_width
                if gallery_height:
                    img_block["image"]["height"] = gallery_height
                idx = len(blocks)
                blocks.append(img_block)
                image_map[idx] = url
                merged = dict(block_def)
                merged.update(item_def)
                image_meta[idx] = _image_metadata(merged)

        elif btype == "bullet":
            elements = _build_elements(block_def.get("elements", []))
            if not elements:
                continue
            blocks.append({"block_type": 12, "bullet": {"elements": elements}})

        elif btype == "ordered":
            elements = _build_elements(block_def.get("elements", []))
            if not elements:
                continue
            blocks.append({"block_type": 13, "ordered": {"elements": elements}})

        elif btype == "quote":
            elements = _build_elements(block_def.get("elements", []))
            if not elements:
                continue
            blocks.append(_quote_block(*elements))

        elif btype == "code":
            text = block_def.get("text", "")
            blocks.append({
                "block_type": 14,
                "code": {
                    "elements": [_text_run(text)],
                    "style": {},
                },
            })

    return blocks, image_map, image_meta


def _image_metadata(block_def: dict) -> dict:
    bucket = block_def.get("bucket", "unclassified")
    alternates = block_def.get("alternates") or block_def.get("fallbacks") or []
    normalized_alternates = []
    for alternate in alternates:
        if isinstance(alternate, str):
            normalized_alternates.append({"url": alternate, "bucket": bucket})
        elif isinstance(alternate, dict) and alternate.get("url"):
            candidate = dict(alternate)
            candidate.setdefault("bucket", bucket)
            normalized_alternates.append(candidate)
    return {
        "bucket": bucket,
        "source_tier": block_def.get("source_tier", "unknown"),
        "source_label": block_def.get("source_label", "unknown"),
        "alternates": normalized_alternates,
    }


# ── Element builders ─────────────────────────────────────

def _build_elements(runs: list[dict]) -> list[dict]:
    """Build text_run elements from run definitions."""
    elements = []
    for run in runs:
        text = run.get("text", "")
        if not text:
            continue
        style = {}
        if run.get("bold"):
            style["bold"] = True
        if run.get("italic"):
            style["italic"] = True
        if run.get("underline"):
            style["underline"] = True
        if run.get("strikethrough"):
            style["strikethrough"] = True
        if run.get("link"):
            style["link"] = {"url": run["link"]}
        if run.get("inline_code"):
            style["inline_code"] = True
        if run.get("color"):
            style["text_color"] = run["color"]
        elements.append({"text_run": {"content": text, "text_element_style": style}})
    return elements


def _text_run(content: str, **kwargs) -> dict:
    """Build a single text_run element."""
    style = {k: v for k, v in kwargs.items() if v}
    return {"text_run": {"content": content, "text_element_style": style}}


def _quote_block(*parts: dict) -> dict:
    """Build a quote block (block_type=15)."""
    return {
        "block_type": 15,
        "quote": {
            "elements": list(parts) if parts else [_text_run("")]
        },
    }
