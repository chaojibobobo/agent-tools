# Feishu Markdown Sync Model

## lark-cli commands

Fetch Feishu to Markdown:

```bash
lark-cli docs +fetch \
  --api-version v2 \
  --doc "<doc_url_or_id>" \
  --doc-format markdown \
  --format json \
  --as user
```

Push Markdown to Feishu:

```bash
lark-cli docs +update \
  --api-version v2 \
  --doc "<doc_url_or_id>" \
  --command overwrite \
  --doc-format markdown \
  --content @push.md \
  --as user
```

`@file` must be relative to the command working directory. The script writes `push.md` inside a temporary run directory before calling `lark-cli`.

## Hash model

The script compares:

- `local_hash`: SHA-256 of local Markdown body excluding sync frontmatter.
- `remote_hash`: SHA-256 of fetched Feishu Markdown.
- `feishu_sync_last_hash`: SHA-256 of the last body known to match both sides.

States:

- `in_sync`: local and remote match.
- `local_changed`: local differs from last sync; remote still matches last sync.
- `remote_changed`: remote differs from last sync; local still matches last sync.
- `conflict`: both local and remote differ from last sync and from each other.
- `unbound`: no `feishu_doc` metadata.

## Conflict behavior

For `pull`, `push`, or `sync`, conflict writes snapshots under:

```text
.feishu-md-sync/
```

Snapshot files:

- `<name>-<timestamp>.local.md`
- `<name>-<timestamp>.remote.md`
- `<name>-<timestamp>.base.txt`

The script exits non-zero and does not overwrite either side.

## Format limitations

Feishu Markdown export/import is not a perfect round trip for all Docx blocks. Avoid relying on this tool for:

- comments,
- complex tables,
- whiteboards,
- sheets and bitables embedded inside docs,
- rich styling that Markdown cannot represent.

For those documents, use `lark-cli docs +fetch --doc-format xml` and block-level updates instead.
