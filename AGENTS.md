# Agent Tools Instructions

这个仓库保存 bobo 的通用 agent tools。默认中文沟通，路径、命令、API、正式英文项目名可保留英文。

## Repository Scope

- `skills/`：Codex / Claude Code / 其他 agent 可复用技能源码。
- `scripts/`：仓库级安装、校验、同步脚本。
- `docs/`：公开可提交的说明、设计记录和使用文档。

## Commit Boundary

默认只提交 system/tool layer：

- skill 源码、脚本、模板、公共参考文档。
- 不含密钥的配置样例。
- 可公开给未来 agent 复用的流程说明。

默认不要提交：

- `_better-me-handoffs/`
- interaction logs、日报、周报、复盘、个人或公司强上下文材料。
- `.env`、tokens、cookies、API keys、private keys、auth cache。
- 真实上传过程中产生的临时 manifest、下载图片、fetch 输出。

## Skill Conventions

- 每个 skill 放在 `skills/<skill-name>/`。
- 必须包含 `SKILL.md`。
- 可选目录：`scripts/`、`references/`、`assets/`、`agents/`。
- 脆弱或重复的操作优先沉到脚本里，`SKILL.md` 保持精简。
- 更新 skill 后至少运行结构校验；如果有脚本，也做 dry-run 或语法校验。

## Installation

源码仓库不是运行时安装目录。Codex skill 安装目录通常是：

```text
/Users/bobo/.codex/skills/<skill-name>/
```

优先使用：

```bash
scripts/install-skill.sh <skill-name>
```

