#!/usr/bin/env python3
"""Upload Markdown with images to a Feishu/Lark cloud document via lark-cli.

The script replaces image markdown with unique placeholder lines, writes the
placeholder markdown to a document, appends local media, moves each media block
after its placeholder block, then deletes placeholders.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen


IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\n]+)\)")
HTML_IMG_RE = re.compile(
    r"<img\b(?=[^>]*\bsrc=[\"']([^\"']+)[\"'])(?=[^>]*)(?:[^>]*)>",
    re.IGNORECASE,
)
ALT_RE = re.compile(r"\balt=[\"']([^\"']*)[\"']", re.IGNORECASE)
FENCE_RE = re.compile(r"^\s*(```|~~~)")
DOC_URL_RE = re.compile(r"https?://[^\s\"']+/docx/[A-Za-z0-9]+")
TOKEN_RE = re.compile(r"(?:document_id|doc_id|token)[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9]{12,})")
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)


@dataclass
class ImageRef:
    index: int
    alt: str
    source: str
    placeholder: str
    local_path: str | None = None
    status: str = "pending"
    error: str | None = None
    uploaded: bool = False
    moved: bool = False
    cleaned: bool = False
    placeholder_block_id: str | None = None
    media_block_id: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload a Markdown file with images to Feishu/Lark Docs."
    )
    parser.add_argument("markdown_file", help="Path to the Markdown file.")
    parser.add_argument("--title", help="Document title. Defaults to first H1 or file stem.")
    parser.add_argument("--doc", help="Existing doc URL or document id to overwrite.")
    parser.add_argument(
        "--parent-token",
        dest="parent_token",
        help="Parent folder token or wiki node token for new documents.",
    )
    parser.add_argument(
        "--folder-token",
        dest="parent_token",
        help="Deprecated alias for --parent-token.",
    )
    parser.add_argument(
        "--wiki-node",
        dest="parent_token",
        help="Deprecated alias for --parent-token.",
    )
    parser.add_argument("--parent-position", help="Parent position, for example my_library.")
    parser.add_argument("--wiki-space", help="Deprecated; use --parent-token or --parent-position.")
    parser.add_argument(
        "--as",
        dest="identity",
        choices=("user", "bot"),
        default="user",
        help="lark-cli identity. Defaults to user for document ownership.",
    )
    parser.add_argument("--api-version", default="v2", choices=("v1", "v2"))
    parser.add_argument("--doc-domain", default="gcnpesuzmfqe.feishu.cn")
    parser.add_argument("--image-width", type=int, default=800)
    parser.add_argument("--rate-limit-seconds", type=float, default=1.2)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--workdir", help="Keep temporary files in this directory.")
    parser.add_argument("--lark-cli", help="Path to lark-cli binary.")
    parser.add_argument("--caption-from-alt", action="store_true")
    parser.add_argument("--keep-placeholders", action="store_true")
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Prepare files but do not call lark-cli.")
    parser.add_argument("--no-download", action="store_true", help="Do not download/copy images.")
    return parser.parse_args()


def find_lark_cli(explicit: str | None = None) -> str:
    if explicit:
        return str(Path(explicit).expanduser())
    home_candidate = Path.home() / ".npm-global/bin/lark-cli"
    if home_candidate.exists():
        return str(home_candidate)
    found = shutil.which("lark-cli")
    if found:
        return found
    raise SystemExit("lark-cli not found. Install/configure lark-cli first.")


def default_title(md_path: Path, text: str) -> str:
    for line in text.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return match.group(1).strip()
    return md_path.stem.replace("-", " ").replace("_", " ").strip() or "Markdown Report"


def apply_explicit_title(body: str, title: str | None) -> str:
    """Make --title affect the Markdown title used by lark-cli +create."""
    if not title:
        return body
    lines = body.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if re.match(r"^#\s+.+", line):
            line_end = "\n" if line.endswith("\n") else ""
            lines[i] = f"# {title}{line_end}"
            return "".join(lines)
        if line.strip():
            break
    return f"# {title}\n\n{body}"


def parse_markdown_destination(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("<"):
        end = raw.find(">")
        if end > 0:
            return raw[1:end].strip()
    if raw.startswith(("'", '"')):
        quote = raw[0]
        end = raw.find(quote, 1)
        if end > 0:
            return raw[1:end]
    return raw.split()[0].strip()


def make_placeholder(index: int) -> str:
    return f"FEISHU_MD_IMAGE_{index:03d}_{uuid.uuid4().hex[:8]}"


def replace_images(text: str) -> tuple[str, list[ImageRef]]:
    refs: list[ImageRef] = []
    out_lines: list[str] = []
    in_fence = False

    def replace_markdown(match: re.Match[str]) -> str:
        idx = len(refs) + 1
        placeholder = make_placeholder(idx)
        refs.append(
            ImageRef(
                index=idx,
                alt=match.group(1).strip(),
                source=parse_markdown_destination(match.group(2)),
                placeholder=placeholder,
            )
        )
        return f"\n\n{placeholder}\n\n"

    def replace_html(match: re.Match[str]) -> str:
        idx = len(refs) + 1
        raw_tag = match.group(0)
        alt_match = ALT_RE.search(raw_tag)
        placeholder = make_placeholder(idx)
        refs.append(
            ImageRef(
                index=idx,
                alt=(alt_match.group(1).strip() if alt_match else ""),
                source=match.group(1).strip(),
                placeholder=placeholder,
            )
        )
        return f"\n\n{placeholder}\n\n"

    for line in text.splitlines(keepends=True):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence:
            out_lines.append(line)
            continue
        line = IMAGE_RE.sub(replace_markdown, line)
        line = HTML_IMG_RE.sub(replace_html, line)
        out_lines.append(line)

    return "".join(out_lines), refs


def extension_from_response(source: str, content_type: str | None) -> str:
    if content_type:
        content_type = content_type.split(";")[0].strip().lower()
        ext = mimetypes.guess_extension(content_type)
        if ext:
            return ".jpg" if ext == ".jpe" else ext
    suffix = Path(urlparse(source).path).suffix
    if suffix and len(suffix) <= 8:
        return suffix
    return ".img"


def resolve_image(ref: ImageRef, md_dir: Path, image_dir: Path, no_download: bool) -> None:
    source = ref.source
    parsed = urlparse(source)
    try:
        if no_download:
            ref.status = "skipped"
            return

        if parsed.scheme in ("http", "https"):
            req = Request(source, headers={"User-Agent": UA})
            with urlopen(req, timeout=30) as resp:
                data = resp.read()
                content_type = resp.headers.get("Content-Type")
            if not data:
                raise RuntimeError("download returned 0 bytes")
            ext = extension_from_response(source, content_type)
            target = image_dir / f"image_{ref.index:03d}{ext}"
            target.write_bytes(data)
            ref.local_path = str(target)
            ref.status = "ready"
            return

        if parsed.scheme == "file":
            src_path = Path(unquote(parsed.path))
        else:
            src_path = Path(os.path.expanduser(source))
            if not src_path.is_absolute():
                src_path = md_dir / src_path

        if not src_path.exists():
            raise FileNotFoundError(str(src_path))

        suffix = src_path.suffix or ".img"
        target = image_dir / f"image_{ref.index:03d}{suffix}"
        shutil.copy2(src_path, target)
        ref.local_path = str(target)
        ref.status = "ready"
    except Exception as exc:  # noqa: BLE001 - manifest should keep exact failure.
        ref.status = "failed"
        ref.error = str(exc)


def run_cmd(cmd: list[str], cwd: Path, dry_run: bool = False) -> subprocess.CompletedProcess[str]:
    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"dry_run": cmd}), stderr="")
    return subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, check=False)


def must_succeed(result: subprocess.CompletedProcess[str], action: str) -> None:
    if result.returncode != 0:
        raise RuntimeError(
            f"{action} failed with exit {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def walk_json(value: Any) -> list[Any]:
    found = [value]
    if isinstance(value, dict):
        for child in value.values():
            found.extend(walk_json(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(walk_json(child))
    return found


def extract_doc_ref(stdout: str, fallback_domain: str) -> tuple[str | None, str | None]:
    url_match = DOC_URL_RE.search(stdout)
    doc_url = url_match.group(0) if url_match else None
    doc_id = None

    try:
        data = json.loads(stdout)
        for item in walk_json(data):
            if isinstance(item, dict):
                for key in ("document_id", "doc_id", "token"):
                    value = item.get(key)
                    if isinstance(value, str) and len(value) >= 12:
                        doc_id = value
                        break
            if doc_id:
                break
    except json.JSONDecodeError:
        token_match = TOKEN_RE.search(stdout)
        if token_match:
            doc_id = token_match.group(1)

    if doc_url is None and doc_id:
        doc_url = f"https://{fallback_domain}/docx/{doc_id}"
    return doc_id, doc_url


def contains_block_id(stdout: str) -> bool:
    if '"block_id"' in stdout:
        return True
    try:
        data = json.loads(stdout)
        for item in walk_json(data):
            if isinstance(item, dict) and item.get("block_id"):
                return True
    except json.JSONDecodeError:
        return False
    return False


def extract_first_block_id(stdout: str) -> str | None:
    try:
        data = json.loads(stdout)
        for item in walk_json(data):
            if isinstance(item, dict):
                value = item.get("block_id")
                if isinstance(value, str) and value:
                    return value
    except json.JSONDecodeError:
        pass
    match = re.search(r'"block_id"\s*:\s*"([^"]+)"', stdout)
    return match.group(1) if match else None


def extract_fetch_content(stdout: str) -> str | None:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    for item in walk_json(data):
        if isinstance(item, dict):
            value = item.get("content")
            if isinstance(value, str):
                return value
    return None


def find_block_id_for_text(content: str, text: str) -> str | None:
    pos = content.find(text)
    if pos < 0:
        return None
    prefix = content[max(0, pos - 1500) : pos]
    matches = list(re.finditer(r'\bid="([^"]+)"', prefix))
    return matches[-1].group(1) if matches else None


def identity_args(args: argparse.Namespace) -> list[str]:
    return ["--as", args.identity] if args.identity else []


def create_or_update_doc(
    args: argparse.Namespace,
    lark_cli: str,
    workdir: Path,
    title: str,
) -> tuple[str, str | None]:
    if args.doc:
        cmd = [
            lark_cli,
            "docs",
            "+update",
            "--doc",
            args.doc,
            "--api-version",
            args.api_version,
            "--command",
            "overwrite",
            "--doc-format",
            "markdown",
            "--content",
            "@body.md",
        ]
        cmd.extend(identity_args(args))
        result = run_cmd(cmd, workdir, args.dry_run)
        must_succeed(result, "update document")
        doc_url = args.doc if args.doc.startswith("http") else f"https://{args.doc_domain}/docx/{args.doc}"
        return args.doc, doc_url

    cmd = [
        lark_cli,
        "docs",
        "+create",
        "--api-version",
        args.api_version,
        "--doc-format",
        "markdown",
        "--content",
        "@body.md",
    ]
    if args.parent_token:
        cmd.extend(["--parent-token", args.parent_token])
    if args.parent_position:
        cmd.extend(["--parent-position", args.parent_position])
    cmd.extend(identity_args(args))

    result = run_cmd(cmd, workdir, args.dry_run)
    must_succeed(result, "create document")
    doc_id, doc_url = extract_doc_ref(result.stdout, args.doc_domain)
    doc_ref = doc_id or doc_url
    if not doc_ref and not args.dry_run:
        raise RuntimeError(f"Could not parse document id/url from lark-cli output:\n{result.stdout}")
    return doc_ref or "DRY_RUN_DOC", doc_url


def upload_one_image(
    args: argparse.Namespace,
    lark_cli: str,
    workdir: Path,
    doc_ref: str,
    ref: ImageRef,
) -> None:
    if not ref.local_path:
        return
    media_path = Path(ref.local_path).resolve()
    try:
        cli_file = str(media_path.relative_to(workdir.resolve()))
    except ValueError:
        cli_file = str(media_path)
    cmd = [
        lark_cli,
        "docs",
        "+media-insert",
        "--doc",
        doc_ref,
        "--file",
        cli_file,
        "--type",
        "image",
        "--width",
        str(args.image_width),
        "--align",
        "center",
    ]
    if args.caption_from_alt and ref.alt:
        cmd.extend(["--caption", ref.alt[:120]])
    cmd.extend(identity_args(args))

    last_output = ""
    for attempt in range(1, args.retries + 1):
        result = run_cmd(cmd, workdir, args.dry_run)
        last_output = (result.stdout or "") + (result.stderr or "")
        media_block_id = extract_first_block_id(result.stdout)
        if result.returncode == 0 and (args.dry_run or media_block_id):
            ref.uploaded = True
            ref.media_block_id = media_block_id
            ref.status = "uploaded"
            return
        time.sleep(args.rate_limit_seconds * attempt)
    ref.status = "failed"
    ref.error = f"media insert failed after {args.retries} tries: {last_output[-600:]}"


def fetch_placeholder_block_ids(
    args: argparse.Namespace,
    lark_cli: str,
    workdir: Path,
    doc_ref: str,
    refs: list[ImageRef],
) -> None:
    if not refs:
        return
    cmd = [
        lark_cli,
        "docs",
        "+fetch",
        "--doc",
        doc_ref,
        "--api-version",
        args.api_version,
        "--doc-format",
        "xml",
        "--detail",
        "with-ids",
        "--format",
        "json",
    ]
    cmd.extend(identity_args(args))

    content = ""
    for attempt in range(1, args.retries + 1):
        result = run_cmd(cmd, workdir, args.dry_run)
        must_succeed(result, "fetch placeholder block ids")
        content = extract_fetch_content(result.stdout) or ""
        (workdir / "fetch-with-ids.xml").write_text(content, encoding="utf-8")
        for ref in refs:
            ref.placeholder_block_id = find_block_id_for_text(content, ref.placeholder)
        if all(ref.placeholder_block_id for ref in refs):
            return
        time.sleep(args.rate_limit_seconds * attempt)

    for ref in refs:
        if not ref.placeholder_block_id:
            ref.status = "failed"
            ref.error = f"placeholder block id not found for {ref.placeholder}"


def move_uploaded_image(
    args: argparse.Namespace,
    lark_cli: str,
    workdir: Path,
    doc_ref: str,
    ref: ImageRef,
) -> None:
    if not ref.uploaded:
        return
    if not ref.placeholder_block_id or not ref.media_block_id:
        ref.status = "failed"
        missing = []
        if not ref.placeholder_block_id:
            missing.append("placeholder_block_id")
        if not ref.media_block_id:
            missing.append("media_block_id")
        ref.error = f"cannot move image, missing {', '.join(missing)}"
        return
    cmd = [
        lark_cli,
        "docs",
        "+update",
        "--doc",
        doc_ref,
        "--api-version",
        args.api_version,
        "--command",
        "block_move_after",
        "--block-id",
        ref.placeholder_block_id,
        "--src-block-ids",
        ref.media_block_id,
    ]
    cmd.extend(identity_args(args))
    result = run_cmd(cmd, workdir, args.dry_run)
    if result.returncode == 0:
        ref.moved = True
        return
    ref.status = "failed"
    ref.error = f"move image failed: {result.stderr or result.stdout}"


def cleanup_placeholder(
    args: argparse.Namespace,
    lark_cli: str,
    workdir: Path,
    doc_ref: str,
    ref: ImageRef,
) -> None:
    if args.keep_placeholders or not ref.uploaded:
        return
    cmd = [
        lark_cli,
        "docs",
        "+update",
        "--doc",
        doc_ref,
        "--api-version",
        args.api_version,
        "--command",
        "block_delete",
        "--block-id",
        ref.placeholder_block_id or "",
    ]
    cmd.extend(identity_args(args))
    result = run_cmd(cmd, workdir, args.dry_run)
    if result.returncode == 0:
        ref.cleaned = True
    elif ref.error:
        ref.error += f"; cleanup failed: {result.stderr or result.stdout}"
    else:
        ref.error = f"cleanup failed: {result.stderr or result.stdout}"


def fetch_verify(
    args: argparse.Namespace,
    lark_cli: str,
    workdir: Path,
    doc_ref: str,
    refs: list[ImageRef],
) -> int | None:
    if args.skip_fetch or args.dry_run:
        return None
    cmd = [
        lark_cli,
        "docs",
        "+fetch",
        "--doc",
        doc_ref,
        "--api-version",
        args.api_version,
        "--doc-format",
        "xml",
        "--detail",
        "with-ids",
        "--format",
        "json",
    ]
    cmd.extend(identity_args(args))
    result = run_cmd(cmd, workdir)
    if result.returncode != 0:
        (workdir / "fetch-output.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
        return None
    content = extract_fetch_content(result.stdout) or result.stdout
    (workdir / "fetch-output.xml").write_text(content, encoding="utf-8")
    return sum(1 for ref in refs if ref.placeholder in content)


def write_manifest(workdir: Path, args: argparse.Namespace, title: str, refs: list[ImageRef]) -> None:
    manifest = {
        "source_markdown": str(Path(args.markdown_file).expanduser().resolve()),
        "title": title,
        "api_version": args.api_version,
        "images": [asdict(ref) for ref in refs],
    }
    (workdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    md_path = Path(args.markdown_file).expanduser().resolve()
    if not md_path.exists():
        print(f"Markdown file not found: {md_path}", file=sys.stderr)
        return 2

    text = md_path.read_text(encoding="utf-8")
    title = args.title or default_title(md_path, text)
    body, refs = replace_images(text)
    body = apply_explicit_title(body, args.title)

    if args.workdir:
        workdir = Path(args.workdir).expanduser().resolve()
        workdir.mkdir(parents=True, exist_ok=True)
    else:
        workdir = Path("/tmp") / f"feishu-md-upload-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        workdir.mkdir(parents=True, exist_ok=False)
    image_dir = workdir / "images"
    image_dir.mkdir(exist_ok=True)

    (workdir / "body.md").write_text(body, encoding="utf-8")

    for ref in refs:
        resolve_image(ref, md_path.parent, image_dir, args.no_download)
    write_manifest(workdir, args, title, refs)

    if args.dry_run:
        summary = {
            "dry_run": True,
            "title": title,
            "workdir": str(workdir),
            "body": str(workdir / "body.md"),
            "manifest": str(workdir / "manifest.json"),
            "images_total": len(refs),
            "images_ready": sum(1 for ref in refs if ref.status == "ready"),
            "images_skipped": sum(1 for ref in refs if ref.status == "skipped"),
            "images_failed": sum(1 for ref in refs if ref.status == "failed"),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    lark_cli = find_lark_cli(args.lark_cli)
    doc_ref, doc_url = create_or_update_doc(args, lark_cli, workdir, title)
    fetch_placeholder_block_ids(args, lark_cli, workdir, doc_ref, refs)
    write_manifest(workdir, args, title, refs)

    for ref in refs:
        if ref.status != "ready":
            continue
        upload_one_image(args, lark_cli, workdir, doc_ref, ref)
        move_uploaded_image(args, lark_cli, workdir, doc_ref, ref)
        if ref.moved:
            cleanup_placeholder(args, lark_cli, workdir, doc_ref, ref)
        time.sleep(args.rate_limit_seconds)
        write_manifest(workdir, args, title, refs)

    placeholders_remaining = fetch_verify(args, lark_cli, workdir, doc_ref, refs)
    write_manifest(workdir, args, title, refs)

    summary = {
        "title": title,
        "doc": doc_ref,
        "url": doc_url or (doc_ref if doc_ref.startswith("http") else None),
        "workdir": str(workdir),
        "images_total": len(refs),
        "images_uploaded": sum(1 for ref in refs if ref.uploaded),
        "images_moved": sum(1 for ref in refs if ref.moved),
        "images_failed": sum(1 for ref in refs if ref.status == "failed"),
        "cleanup_failed": sum(
            1 for ref in refs if ref.uploaded and not ref.cleaned and not args.keep_placeholders
        ),
        "placeholders_remaining": placeholders_remaining,
        "manifest": str(workdir / "manifest.json"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["images_failed"]:
        return 1
    if placeholders_remaining and placeholders_remaining > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
