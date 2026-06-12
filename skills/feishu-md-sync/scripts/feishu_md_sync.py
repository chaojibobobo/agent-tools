#!/usr/bin/env python3
"""Safely synchronize a local Markdown file with a Feishu/Lark document."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SYNC_KEYS = {
    "feishu_doc",
    "feishu_doc_id",
    "feishu_sync_last_hash",
    "feishu_sync_last_remote_revision",
    "feishu_sync_last_at",
}
DOC_ID_RE = re.compile(r"/docx/([A-Za-z0-9]+)|\b([A-Za-z0-9]{12,})\b")


@dataclass
class MarkdownFile:
    path: Path
    frontmatter: str
    body: str
    metadata: dict[str, str]


@dataclass
class RemoteDoc:
    doc: str
    body: str
    revision_id: str | None = None


def parse_args() -> argparse.Namespace:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--as", dest="identity", choices=("user", "bot"), default="user")
    common.add_argument("--lark-cli", help="Path to lark-cli binary.")
    common.add_argument("--mock-remote-file", help=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(description="Safely sync local Markdown with Feishu Docs.")
    sub = parser.add_subparsers(dest="command", required=True)

    bind = sub.add_parser("bind", parents=[common], help="Bind a local md file to a Feishu doc.")
    bind.add_argument("file")
    bind.add_argument("doc")
    bind.add_argument("--baseline", choices=("local", "remote"), help="Choose baseline when local and remote differ.")
    bind.add_argument("--json", action="store_true")

    status = sub.add_parser("status", parents=[common], help="Show local/remote sync status.")
    status.add_argument("file")
    status.add_argument("--doc", help="Override Feishu doc URL/id.")
    status.add_argument("--json", action="store_true")

    pull = sub.add_parser("pull", parents=[common], help="Pull Feishu Markdown into local file.")
    pull.add_argument("file")
    pull.add_argument("--doc", help="Override Feishu doc URL/id.")
    pull.add_argument("--force", action="store_true", help="Overwrite local even if local changed.")
    pull.add_argument("--json", action="store_true")

    push = sub.add_parser("push", parents=[common], help="Push local Markdown body to Feishu.")
    push.add_argument("file")
    push.add_argument("--doc", help="Override Feishu doc URL/id.")
    push.add_argument("--force", action="store_true", help="Overwrite remote even if remote changed.")
    push.add_argument("--dry-run", action="store_true", help="Pass --dry-run to lark-cli update.")
    push.add_argument("--json", action="store_true")

    sync = sub.add_parser("sync", parents=[common], help="Safely auto pull or push when only one side changed.")
    sync.add_argument("file")
    sync.add_argument("--doc", help="Override Feishu doc URL/id.")
    sync.add_argument("--json", action="store_true")

    return parser.parse_args()


def find_lark_cli(explicit: str | None = None) -> str:
    if explicit:
        return str(Path(explicit).expanduser())
    candidate = Path.home() / ".npm-global/bin/lark-cli"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("lark-cli")
    if found:
        return found
    raise RuntimeError("lark-cli not found")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_body(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return normalized + "\n" if normalized else ""


def sha256_body(text: str) -> str:
    return hashlib.sha256(normalize_body(text).encode("utf-8")).hexdigest()


def split_frontmatter(text: str) -> tuple[str, str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("---\n"):
        return "", text
    lines = text.splitlines(keepends=True)
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1 :])
    return "", text


def unquote_yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_metadata(frontmatter: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in frontmatter.splitlines():
        match = re.match(r"^([A-Za-z0-9_]+):\s*(.*?)\s*$", line)
        if match:
            metadata[match.group(1)] = unquote_yaml_scalar(match.group(2))
    return metadata


def load_markdown(path: Path) -> MarkdownFile:
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = ""
    frontmatter, body = split_frontmatter(text)
    return MarkdownFile(path=path, frontmatter=frontmatter, body=body, metadata=parse_metadata(frontmatter))


def render_frontmatter(existing: str, updates: dict[str, str | int | None]) -> str:
    kept: list[str] = []
    for line in existing.splitlines():
        match = re.match(r"^([A-Za-z0-9_]+):", line)
        if match and match.group(1) in SYNC_KEYS:
            continue
        kept.append(line)

    while kept and not kept[-1].strip():
        kept.pop()

    for key in sorted(SYNC_KEYS):
        value = updates.get(key)
        if value is not None and value != "":
            kept.append(f"{key}: {json.dumps(str(value), ensure_ascii=False)}")
    return "\n".join(kept).strip() + "\n"


def write_markdown(md: MarkdownFile, body: str, updates: dict[str, str | int | None]) -> None:
    frontmatter = render_frontmatter(md.frontmatter, updates)
    md.path.parent.mkdir(parents=True, exist_ok=True)
    md.path.write_text(f"---\n{frontmatter}---\n\n{body.lstrip()}", encoding="utf-8")


def extract_doc_id(doc: str) -> str:
    match = DOC_ID_RE.search(doc)
    if not match:
        return ""
    return next(group for group in match.groups() if group)


def walk_json(value: Any) -> list[Any]:
    found = [value]
    if isinstance(value, dict):
        for child in value.values():
            found.extend(walk_json(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(walk_json(child))
    return found


def run_cmd(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, check=False)


def parse_fetch(stdout: str, doc: str) -> RemoteDoc:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse lark-cli fetch JSON: {stdout[:500]}") from exc

    content: str | None = None
    revision_id: str | None = None
    for item in walk_json(data):
        if isinstance(item, dict):
            if content is None and isinstance(item.get("content"), str):
                content = item["content"]
            if revision_id is None and item.get("revision_id") is not None:
                revision_id = str(item["revision_id"])
    if content is None:
        raise RuntimeError(f"lark-cli fetch returned no document content: {stdout[:500]}")
    return RemoteDoc(doc=doc, body=content, revision_id=revision_id)


def fetch_remote(args: argparse.Namespace, doc: str) -> RemoteDoc:
    if args.mock_remote_file:
        body = Path(args.mock_remote_file).expanduser().read_text(encoding="utf-8")
        return RemoteDoc(doc=doc, body=body, revision_id="mock")

    cmd = [
        find_lark_cli(args.lark_cli),
        "docs",
        "+fetch",
        "--api-version",
        "v2",
        "--doc",
        doc,
        "--doc-format",
        "markdown",
        "--format",
        "json",
        "--as",
        args.identity,
    ]
    result = run_cmd(cmd, Path.cwd())
    if result.returncode != 0:
        raise RuntimeError(f"fetch failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    return parse_fetch(result.stdout, doc)


def push_remote(args: argparse.Namespace, doc: str, body: str) -> None:
    if args.mock_remote_file:
        if getattr(args, "dry_run", False):
            return
        Path(args.mock_remote_file).expanduser().write_text(body, encoding="utf-8")
        return

    with tempfile.TemporaryDirectory(prefix="feishu-md-sync-") as tmp:
        workdir = Path(tmp)
        (workdir / "push.md").write_text(body, encoding="utf-8")
        cmd = [
            find_lark_cli(args.lark_cli),
            "docs",
            "+update",
            "--api-version",
            "v2",
            "--doc",
            doc,
            "--command",
            "overwrite",
            "--doc-format",
            "markdown",
            "--content",
            "@push.md",
            "--as",
            args.identity,
        ]
        if getattr(args, "dry_run", False):
            cmd.append("--dry-run")
        result = run_cmd(cmd, workdir)
        if result.returncode != 0:
            raise RuntimeError(f"push failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")


def resolve_doc(args: argparse.Namespace, md: MarkdownFile) -> str:
    doc = getattr(args, "doc", None) or md.metadata.get("feishu_doc")
    if not doc:
        raise RuntimeError("Markdown file is not bound. Run bind first or pass --doc.")
    return doc


def sync_updates(doc: str, remote: RemoteDoc, body_hash: str) -> dict[str, str | int | None]:
    return {
        "feishu_doc": doc,
        "feishu_doc_id": extract_doc_id(doc),
        "feishu_sync_last_hash": body_hash,
        "feishu_sync_last_remote_revision": remote.revision_id,
        "feishu_sync_last_at": now_iso(),
    }


def classify(md: MarkdownFile, remote: RemoteDoc) -> dict[str, Any]:
    local_hash = sha256_body(md.body)
    remote_hash = sha256_body(remote.body)
    last_hash = md.metadata.get("feishu_sync_last_hash", "")

    local_changed = bool(last_hash) and local_hash != last_hash
    remote_changed = bool(last_hash) and remote_hash != last_hash

    if not last_hash:
        state = "unbaselined"
    elif local_hash == remote_hash:
        state = "in_sync"
        local_changed = False
        remote_changed = False
    elif local_changed and remote_changed:
        state = "conflict"
    elif local_changed:
        state = "local_changed"
    elif remote_changed:
        state = "remote_changed"
    else:
        state = "drift"

    return {
        "state": state,
        "local_hash": local_hash,
        "remote_hash": remote_hash,
        "last_sync_hash": last_hash,
        "local_changed": local_changed,
        "remote_changed": remote_changed,
        "remote_revision": remote.revision_id,
        "doc": remote.doc,
    }


def conflict_snapshots(md: MarkdownFile, remote: RemoteDoc, status: dict[str, Any]) -> dict[str, str]:
    out_dir = md.path.parent / ".feishu-md-sync"
    out_dir.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = md.path.stem
    local_path = out_dir / f"{stem}-{stamp}.local.md"
    remote_path = out_dir / f"{stem}-{stamp}.remote.md"
    base_path = out_dir / f"{stem}-{stamp}.base.txt"
    local_path.write_text(md.body, encoding="utf-8")
    remote_path.write_text(remote.body, encoding="utf-8")
    base_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"local": str(local_path), "remote": str(remote_path), "base": str(base_path)}


def print_result(result: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    state = result.get("state") or result.get("action") or "ok"
    print(json.dumps({"state": state, **result}, ensure_ascii=False, indent=2))


def cmd_bind(args: argparse.Namespace) -> int:
    md = load_markdown(Path(args.file).expanduser().resolve())
    remote = fetch_remote(args, args.doc)
    local_body = md.body
    local_empty = not normalize_body(local_body)
    local_hash = sha256_body(local_body)
    remote_hash = sha256_body(remote.body)

    if local_empty or args.baseline == "remote":
        write_markdown(md, remote.body, sync_updates(args.doc, remote, remote_hash))
        print_result({"action": "bound", "baseline": "remote", "file": str(md.path), "doc": args.doc}, args.json)
        return 0

    if args.baseline == "local" or local_hash == remote_hash:
        write_markdown(md, local_body, sync_updates(args.doc, remote, local_hash))
        print_result({"action": "bound", "baseline": "local", "file": str(md.path), "doc": args.doc}, args.json)
        return 0

    print_result(
        {
            "state": "bind_requires_baseline",
            "message": "Local and remote content differ. Re-run with --baseline local or --baseline remote.",
            "local_hash": local_hash,
            "remote_hash": remote_hash,
        },
        args.json,
    )
    return 1


def cmd_status(args: argparse.Namespace) -> int:
    md = load_markdown(Path(args.file).expanduser().resolve())
    doc = resolve_doc(args, md)
    remote = fetch_remote(args, doc)
    result = classify(md, remote)
    print_result(result, args.json)
    return 0 if result["state"] != "conflict" else 1


def cmd_pull(args: argparse.Namespace) -> int:
    md = load_markdown(Path(args.file).expanduser().resolve())
    doc = resolve_doc(args, md)
    remote = fetch_remote(args, doc)
    status = classify(md, remote)

    if status["state"] == "conflict" and not args.force:
        status["snapshots"] = conflict_snapshots(md, remote, status)
        print_result(status, args.json)
        return 1
    if status["state"] == "local_changed" and not args.force:
        status["state"] = "would_overwrite_local"
        status["message"] = "Local changed while remote did not. Use --force to replace local with remote."
        print_result(status, args.json)
        return 1

    remote_hash = sha256_body(remote.body)
    write_markdown(md, remote.body, sync_updates(doc, remote, remote_hash))
    status["action"] = "pulled"
    status["state"] = "in_sync"
    print_result(status, args.json)
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    md = load_markdown(Path(args.file).expanduser().resolve())
    doc = resolve_doc(args, md)
    remote = fetch_remote(args, doc)
    status = classify(md, remote)

    if status["state"] == "conflict" and not args.force:
        status["snapshots"] = conflict_snapshots(md, remote, status)
        print_result(status, args.json)
        return 1
    if status["state"] == "remote_changed" and not args.force:
        status["state"] = "would_overwrite_remote"
        status["message"] = "Remote changed while local did not. Pull first or use --force."
        print_result(status, args.json)
        return 1

    push_remote(args, doc, md.body)
    if not args.dry_run:
        local_hash = sha256_body(md.body)
        write_markdown(md, md.body, sync_updates(doc, remote, local_hash))
    status["action"] = "pushed_dry_run" if args.dry_run else "pushed"
    status["state"] = "in_sync" if not args.dry_run else status["state"]
    print_result(status, args.json)
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    md = load_markdown(Path(args.file).expanduser().resolve())
    doc = resolve_doc(args, md)
    remote = fetch_remote(args, doc)
    status = classify(md, remote)

    if status["state"] == "in_sync":
        write_markdown(md, md.body, sync_updates(doc, remote, sha256_body(md.body)))
        status["action"] = "noop"
        print_result(status, args.json)
        return 0
    if status["state"] == "remote_changed":
        remote_hash = sha256_body(remote.body)
        write_markdown(md, remote.body, sync_updates(doc, remote, remote_hash))
        status["action"] = "pulled"
        status["state"] = "in_sync"
        print_result(status, args.json)
        return 0
    if status["state"] == "local_changed":
        push_remote(args, doc, md.body)
        write_markdown(md, md.body, sync_updates(doc, remote, sha256_body(md.body)))
        status["action"] = "pushed"
        status["state"] = "in_sync"
        print_result(status, args.json)
        return 0

    status["state"] = "conflict"
    status["snapshots"] = conflict_snapshots(md, remote, status)
    print_result(status, args.json)
    return 1


def main() -> int:
    args = parse_args()
    try:
        if args.command == "bind":
            return cmd_bind(args)
        if args.command == "status":
            return cmd_status(args)
        if args.command == "pull":
            return cmd_pull(args)
        if args.command == "push":
            return cmd_push(args)
        if args.command == "sync":
            return cmd_sync(args)
    except Exception as exc:  # noqa: BLE001 - CLI should return structured error.
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
