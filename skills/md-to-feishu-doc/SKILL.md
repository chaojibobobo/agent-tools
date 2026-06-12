---
name: md-to-feishu-doc
description: Upload a local Markdown file with local or remote images into a Feishu/Lark cloud document. Use when the user wants to publish, import, sync, or upload a .md report with images to Feishu Docs, Lark Docs, or Feishu cloud documents.
metadata:
  short-description: Upload image Markdown to Feishu Docs
---

# Markdown 带图上传飞书云文档

把本地 Markdown 报告发布到飞书/Lark Docx 文档，并尽量保留图片位置。默认使用新版 `lark-cli docs` v2：先写入去图版 Markdown，再追加上传图片，回读 block id 后把图片移动到占位符后方，最后删除占位符并回读校验。

## 触发场景

- 用户说“把这个 md 发到飞书”“上传到飞书云文档”“生成飞书版”“带图 Markdown 导入飞书”。
- 输入是 `.md` 文件，图片可能是相对路径、绝对路径、`file://`、`http(s)` URL。
- 目标可以是新建飞书文档，也可以覆盖已有 docx 文档。

## 优先工具

首选脚本：

```bash
python3 skills/md-to-feishu-doc/scripts/md_to_feishu_doc.py <markdown-file>
```

常用参数：

```bash
python3 skills/md-to-feishu-doc/scripts/md_to_feishu_doc.py report.md \
  --title "飞书文档标题" \
  --parent-token "<folder_or_wiki_node_token>" \
  --api-version v2
```

覆盖已有文档：

```bash
python3 skills/md-to-feishu-doc/scripts/md_to_feishu_doc.py report.md \
  --doc "https://example.feishu.cn/docx/xxxxxxxxxxxx" \
  --title "新标题" \
  --api-version v2
```

先做本地预检，不调用飞书：

```bash
python3 skills/md-to-feishu-doc/scripts/md_to_feishu_doc.py report.md --dry-run --no-download
```

## 工作流

1. 检查 `lark-cli` 是否可用：优先 `~/.npm-global/bin/lark-cli`，否则用 PATH。
2. 读取 Markdown，跳过代码块内的图片语法。
3. 把每个图片引用替换成唯一占位符行，生成临时 `body.md`。
4. 下载或复制图片到临时工作目录，生成 `manifest.json`。
5. 新建文档或覆盖已有文档：
   - 新建：`lark-cli docs +create --api-version v2 --doc-format markdown --content @body.md`
   - 覆盖：`lark-cli docs +update --api-version v2 --command overwrite --doc-format markdown --content @body.md`
6. `docs +fetch --detail with-ids` 回读占位符 block id。
7. 对每张图片执行 `docs +media-insert --file images/...`，图片路径必须是当前工作目录下的相对路径。
8. 用 `docs +update --command block_move_after` 把图片 block 移到占位符后。
9. 用 `docs +update --command block_delete` 删除占位符 block。
10. 用 `docs +fetch` 回读，报告剩余占位符、成功图片数、移动图片数、失败图片数和文档 URL。

## 重要判断

- 新版 `lark-cli` 使用 `--content` / `--command` / `--doc-format markdown`，不要再用旧的 `--markdown` / `--mode`。
- `docs +update --content` 不应被假设为能上传本地图片。图片必须单独下载或复制后插入。
- `--command overwrite` 会清掉原文档里的所有 block，包括之前插入的图片。覆盖后必须重新插入全部图片。
- `docs +media-insert --file` 只传工作目录下的相对路径；绝对路径会触发 `unsafe file path`。
- 占位符清理是 best effort。如果清理失败，不要谎报完成；把剩余占位符数量告诉用户。
- 如果图片非常多，保留默认节流；遇到限流时加大 `--rate-limit-seconds` 或重跑失败项。
- 如果文档已经在团队里共享，覆盖已有文档前先确认用户确实要覆盖。

## 参考

需要排查 API 或工具行为时，只读需要的部分：

- `references/feishu-md-upload.md`：飞书/lark-cli 关键限制、已验证命令、错误处理。
