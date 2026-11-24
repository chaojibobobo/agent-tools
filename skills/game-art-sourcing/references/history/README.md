# Game Art Sourcing 历史资产

## 来源

- 旧工作副本为独立的 `game-art-sourcing` 仓库。
- 旧安装方式通过软链接引用独立工作副本。
- 迁移时本地 HEAD：`2728d0b`
- 迁入日期：2026-07-30

历史文件用于审计旧实现与迁移判断，不是当前 skill 的执行入口。

## 已迁入

- `source-git-log.md`：旧独立仓库的提交脉络。
- `legacy/converter.py`：旧 HTML → Feishu Block 转换器。
- `legacy/feishu_client.py`：迁移时工作副本的飞书客户端快照，包含当时未提交的超时调整。
- `legacy/publish_to_feishu.py`：旧 JSON/HTML 双模式发布脚本。

旧代码保留是为了追溯，不应从 `legacy/` 直接运行。当前运行链只支持 JSON 报告。

## 迁移适配

- 将 Claude 专属路径改成可覆盖的 Codex skill 根目录。
- 移除运行时对 `.claude/settings.json` 和 Claude 子代理接口的依赖。
- 凭证迁出安装目录，默认使用 `~/.config/game-art-sourcing/config.yaml`。
- 缓存迁出安装目录，默认使用 `~/.cache/game-art-sourcing/`。
- 飞书个人权限由自动授予改成 `--grant-user-access` 显式 opt-in。
- 发布前增加 `--validate-only`，无网络、无凭证检查 JSON → Block 转换。
- 重参考内容拆到 `references/`，缩短每次触发时加载的主 skill。

## 明确未迁入

- `.git/`：不嵌套旧仓库元数据，以提交日志文档替代。
- `.omc/`：agent 会话和 HUD 状态不属于公共 tool layer。
- `.claude/settings.json`：本机命令 allow-list 不可作为跨 runtime 权限。
- `config.yaml`：含真实凭证，只可进入本机外部配置路径。
- `cache/`、`operations/` 和个人项目记忆保存在本机。
- 异常 `.pub` 文件、`.DS_Store`、`__pycache__/` 和临时产物。
