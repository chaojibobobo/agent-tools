---
name: feishu-md-sync
description: Safely synchronize a local Markdown file with a Feishu/Lark cloud document. Use when the user wants local .md edits and Feishu Docs edits to stay consistent, with pull, push, status, bind, and conflict detection.
metadata:
  short-description: Sync Markdown with Feishu Docs
---

# Feishu Markdown Sync

安全同步本地 Markdown 文件和飞书/Lark 云文档。它不是实时协同编辑器，而是一个显式同步工具：每次编辑前后执行 `status` / `pull` / `push`，如果本地和飞书两边都改过，会停止并产出冲突快照，避免覆盖。

## 首选脚本

```bash
python3 skills/feishu-md-sync/scripts/feishu_md_sync.py <command> <file.md>
```

## 常用流程

绑定本地 md 和飞书文档：

```bash
python3 skills/feishu-md-sync/scripts/feishu_md_sync.py bind notes.md "https://xxx.feishu.cn/docx/xxxx"
```

如果本地和飞书内容不同，必须显式选择基线：

```bash
python3 skills/feishu-md-sync/scripts/feishu_md_sync.py bind notes.md "<doc>" --baseline local
python3 skills/feishu-md-sync/scripts/feishu_md_sync.py bind notes.md "<doc>" --baseline remote
```

看状态：

```bash
python3 skills/feishu-md-sync/scripts/feishu_md_sync.py status notes.md
```

飞书改了，本地拉取：

```bash
python3 skills/feishu-md-sync/scripts/feishu_md_sync.py pull notes.md
```

本地改了，推回飞书：

```bash
python3 skills/feishu-md-sync/scripts/feishu_md_sync.py push notes.md
```

自动安全同步：

```bash
python3 skills/feishu-md-sync/scripts/feishu_md_sync.py sync notes.md
```

## 同步规则

- 每个 `.md` 的 frontmatter 中记录绑定关系和上次同步 hash。
- `status` 会比较三份状态：本地正文 hash、飞书远端 Markdown hash、上次同步 hash。
- 只有一边改变时，`pull` / `push` / `sync` 可以自动执行。
- 两边都改变且内容不同，则进入 `conflict`，脚本会写入 `.feishu-md-sync/` 快照文件。
- `--force` 可以覆盖，但默认不要用，除非用户明确要覆盖一边。

## Frontmatter

脚本会维护这些字段：

```yaml
---
feishu_doc: "https://xxx.feishu.cn/docx/xxxx"
feishu_doc_id: "xxxx"
feishu_sync_last_hash: "sha256..."
feishu_sync_last_remote_revision: "123"
feishu_sync_last_at: "2026-06-12T12:00:00+08:00"
---
```

正文同步时不会把这些同步字段推到飞书。

## 边界

- 适合纯 Markdown / 轻量图文文档。
- 不保证飞书复杂结构完全等价：评论、画板、复杂表格、嵌入表格、多维表格、部分样式可能无法保真。
- 带本地图片的首次发布仍优先用 `md-to-feishu-doc`；本 skill 更适合后续文字同步。
- 需要飞书权限时，按 `lark-cli` 提示完成 `auth login` 或 scope 授权。

## 参考

- `references/sync-model.md`：同步模型、冲突规则和新版 `lark-cli` 命令。
