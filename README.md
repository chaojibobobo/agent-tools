# Agent Tools

bobo 的通用 agent 工具箱，用来沉淀可跨 Codex、Claude Code 和其他 AI 编程助手复用的 skills、脚本、参考资料和自动化工具。

## Layout

```text
.
├── skills/        # 可安装到 agent runtime 的技能源码
├── scripts/       # 仓库级辅助脚本
└── docs/          # 公共说明和设计记录
```

## Skills

### md-to-feishu-doc

把带图片的 Markdown 上传到飞书/Lark 云文档。

源码位置：

```text
skills/md-to-feishu-doc/
```

安装到 Codex：

```bash
scripts/install-skill.sh md-to-feishu-doc
```

预检一个 Markdown：

```bash
python3 skills/md-to-feishu-doc/scripts/md_to_feishu_doc.py report.md --dry-run --no-download
```

真实上传：

```bash
python3 skills/md-to-feishu-doc/scripts/md_to_feishu_doc.py report.md --title "文档标题"
```

## Privacy

这个仓库默认只提交 system/tool layer：可复用 skill、脚本、公开说明和无敏感信息的技术参考。

不要提交：

- secrets、tokens、cookies、private keys、credentials
- 本地交互日志、handoff、日报、复盘、个人/公司强上下文材料
- `.env`、临时产物、下载缓存、上传 manifest

