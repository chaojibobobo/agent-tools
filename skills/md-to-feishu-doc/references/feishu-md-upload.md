# Feishu Markdown Image Upload Notes

## Official API surfaces

Checked on 2026-06-11. Local `lark-cli` compatibility updated on 2026-06-12 for embedded `lark-doc` skill v2.0.0.

- Create document: `https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/create`
- Create blocks: `https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block/create`
- Upload media: `https://open.feishu.cn/document/server-docs/docs/drive-v1/media/upload_all`

The local workflow intentionally uses `lark-cli docs` first because it already wraps auth, document update, media insert, and fetch behavior in a way that matches bobo's existing Feishu setup.

## Local lark-cli commands

Create:

```bash
lark-cli docs +create \
  --api-version v2 \
  --doc-format markdown \
  --content @body.md
```

Create in a folder or wiki node:

```bash
lark-cli docs +create \
  --api-version v2 \
  --parent-token "<folder_or_wiki_node_token>" \
  --doc-format markdown \
  --content @body.md
```

Overwrite:

```bash
lark-cli docs +update \
  --doc "<doc-url-or-id>" \
  --api-version v2 \
  --command overwrite \
  --doc-format markdown \
  --content @body.md
```

Fetch placeholder block ids:

```bash
lark-cli docs +fetch \
  --doc "<doc-url-or-id>" \
  --api-version v2 \
  --doc-format xml \
  --detail with-ids \
  --format json
```

Append image, then move it after a placeholder block:

```bash
lark-cli docs +media-insert \
  --doc "<doc-url-or-id>" \
  --file images/image_001.png \
  --type image \
  --width 800 \
  --align center

lark-cli docs +update \
  --doc "<doc-url-or-id>" \
  --api-version v2 \
  --command block_move_after \
  --block-id "<placeholder_block_id>" \
  --src-block-ids "<image_block_id>"
```

Delete placeholder block:

```bash
lark-cli docs +update \
  --doc "<doc-url-or-id>" \
  --api-version v2 \
  --command block_delete \
  --block-id "<placeholder_block_id>"
```

Fetch for verification:

```bash
lark-cli docs +fetch \
  --doc "<doc-url-or-id>" \
  --api-version v2 \
  --doc-format xml \
  --detail with-ids \
  --format json
```

## Known gotchas

- Use `lark-cli skills read lark-doc` and the referenced files when CLI behavior changes; the embedded skill is version-matched with the installed CLI.
- `lark-cli` `@file` arguments must be relative to the command cwd, for example `@body.md`. Absolute `@/tmp/file.md` can fail as `unsafe file path`.
- `docs +media-insert --file` also expects a relative path under cwd. Copy/download images into the run directory and pass `images/image_001.png`, not an absolute path.
- `docs +update --command overwrite` deletes existing image blocks. Reinsert every image after every overwrite.
- `docs +media-insert` reports success with a JSON response containing a `block_id`. Treat missing `block_id` as suspicious even if the process exits zero.
- The stable image placement workflow is: placeholder text -> fetch placeholder block ids -> append media -> `block_move_after` -> `block_delete`.
- `selection-with-ellipsis` is still available, but block-id movement is more reliable for batch image placement.
- Permission failures must be separated: missing scope, `unsafe file path`, and `forBidden` usually have different fixes.
- For WeChat article images, prefer extracting real `data-src` URLs from the page HTML instead of trusting transformed Markdown image URLs. This skill is general Markdown, so it does not implement WeChat-specific scraping.
- Avoid `grep -P` in macOS shell validation. Use Python, `sed`, or POSIX-compatible grep.

## When to fall back to direct API

Use direct Feishu API only if `lark-cli docs` cannot express the operation:

- create empty image block through Docx block API,
- upload media with `drive/v1/medias/upload_all`,
- patch the image block with the returned file token,
- verify children under the document root.

That route is more code and more auth-sensitive, so keep it as a fallback, not the default.
