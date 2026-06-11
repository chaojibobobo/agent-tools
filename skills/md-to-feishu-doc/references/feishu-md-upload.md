# Feishu Markdown Image Upload Notes

## Official API surfaces

Checked on 2026-06-11:

- Create document: `https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/create`
- Create blocks: `https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block/create`
- Upload media: `https://open.feishu.cn/document/server-docs/docs/drive-v1/media/upload_all`

The local workflow intentionally uses `lark-cli docs` first because it already wraps auth, document update, media insert, and fetch behavior in a way that matches bobo's existing Feishu setup.

## Local lark-cli commands

Create:

```bash
lark-cli docs +create \
  --api-version v2 \
  --title "Title" \
  --markdown @body.md
```

Overwrite:

```bash
lark-cli docs +update \
  --doc "<doc-url-or-id>" \
  --api-version v2 \
  --mode overwrite \
  --markdown @body.md \
  --new-title "Title"
```

Insert image before a placeholder:

```bash
lark-cli docs +media-insert \
  --doc "<doc-url-or-id>" \
  --file image_001.png \
  --type image \
  --width 800 \
  --before \
  --selection-with-ellipsis "FEISHU_MD_IMAGE_001_xxxxxxxx"
```

Delete placeholder:

```bash
lark-cli docs +update \
  --doc "<doc-url-or-id>" \
  --api-version v2 \
  --mode delete_range \
  --selection-with-ellipsis "FEISHU_MD_IMAGE_001_xxxxxxxx" \
  --markdown ""
```

Fetch for verification:

```bash
lark-cli docs +fetch \
  --doc "<doc-url-or-id>" \
  --api-version v2 \
  --format pretty
```

## Known gotchas

- `lark-cli` `@file` arguments are most reliable when the command runs with `cwd` set to the directory containing the file and the argument is relative, for example `@body.md`.
- `docs +update --mode overwrite` deletes existing image blocks. Reinsert every image after every overwrite.
- `docs +media-insert` reports success with a JSON response containing a `block_id`. Treat missing `block_id` as suspicious even if the process exits zero.
- `selection-with-ellipsis` can match the wrong location if the text is not unique. Generated placeholders avoid this.
- If placeholder deletion fails, the document can still contain uploaded images; report the leftover placeholders and rerun cleanup after checking fetch output.
- For WeChat article images, prefer extracting real `data-src` URLs from the page HTML instead of trusting transformed Markdown image URLs. This skill is general Markdown, so it does not implement WeChat-specific scraping.
- Avoid `grep -P` in macOS shell validation. Use Python, `sed`, or POSIX-compatible grep.

## When to fall back to direct API

Use direct Feishu API only if `lark-cli docs` cannot express the operation:

- create empty image block through Docx block API,
- upload media with `drive/v1/medias/upload_all`,
- patch the image block with the returned file token,
- verify children under the document root.

That route is more code and more auth-sensitive, so keep it as a fallback, not the default.
