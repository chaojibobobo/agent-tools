# Game Scan YouTube

面向移动游戏竞品情报的 YouTube 增量扫描 skill。它以频道 RSS 为稳定骨架，补充网页搜索和商店信号，按 `seen_games.json` 去重，生成日期报告，并可推送到飞书。

本目录从 Claude Code 真源迁入，已适配 Codex；核心筛选逻辑、历史报告和持续状态均保留。

## 目录

```text
game-scan-youtube/
├── SKILL.md
├── README.md
├── agents/openai.yaml
├── scripts/
│   ├── init_workspace.sh
│   ├── collect_rss.py
│   ├── validate_run.py
│   ├── commit_state.py
│   ├── run_scan.sh
│   ├── run_daily_once.sh
│   ├── decision_card.py
│   └── push_feishu.py
├── assets/bootstrap/
│   ├── channels.json
│   └── seen_games.json
└── references/history/
    ├── reports/
    ├── investigations/
    └── operations/
```

## 安装到 Codex

在 `tools` 仓库根目录执行：

```bash
scripts/install-skill.sh game-scan-youtube
```

安装后下一轮 Codex 对话可自动发现此 skill。

## 初始化持续工作区

skill 源码和历史证据保持只读；新的报告、去重状态和运行锁写入独立工作区：

```bash
skills/game-scan-youtube/scripts/init_workspace.sh
```

默认位置：

```text
~/.game-scan-youtube/workspace
```

指定其他位置：

```bash
skills/game-scan-youtube/scripts/init_workspace.sh --dir /path/to/workspace
```

也可以设置：

```bash
export GAME_SCAN_WORK_DIR=/path/to/workspace
```

初始化只补缺失文件，不覆盖已有报告或状态。

## 使用

交互使用时，可以直接告诉 Codex：

```text
运行 game-scan-youtube，扫描今天的新移动策略游戏视频。
```

非交互运行。脚本会先在父进程采集 RSS，再让 agent 只分析本地证据：

```bash
skills/game-scan-youtube/scripts/run_scan.sh --dry-run
```

指定日期和工作区：

```bash
skills/game-scan-youtube/scripts/run_scan.sh \
  --date 2026-07-25 \
  --dir /path/to/workspace \
  --dry-run
```

默认调用 Codex CLI。若仍需用 Claude Code 执行同一 skill：

```bash
skills/game-scan-youtube/scripts/run_scan.sh --agent claude --budget 1.0
```

## 飞书推送

凭据只放在环境变量或 `~/.game-scan-youtube/.env`，不要进入仓库：

```text
FEISHU_APP_ID=...
FEISHU_APP_SECRET=...
FEISHU_USER_OPEN_ID=...
```

先对历史报告做本地解析和渲染预检：

```bash
python3 skills/game-scan-youtube/scripts/push_feishu.py \
  --date 2026-06-16 \
  --dir ~/.game-scan-youtube/workspace \
  --dry-run
```

真实推送：

```bash
python3 skills/game-scan-youtube/scripts/push_feishu.py \
  --date YYYY-MM-DD \
  --dir ~/.game-scan-youtube/workspace
```

飞书输出默认采用 Decision Card v3：

```text
价值结论
→ 必看（默认 1 款，最多 2 款）
→ 为什么看（1 条最强证据）
→ 核心机制（最多 3 个节点）
→ 补充（每款 1 行，并带独立 YouTube 原视频链接）
→ 趋势一句话
→ 观察与更新（数量优先）
```

聊天卡片正常正文控制在约 500 个中文字符、15 行以内。标题直接回答“今天值不值得看”，并用颜色与标签强化必看、补充和观察数量。本地 Markdown 继续保留完整证据。

每一款确认新增游戏都必须把对应的一手 YouTube 视频链接传递到飞书：必看项目显示 `YouTube 原视频` 按钮，补充项目在同一行显示独立的 `YouTube 原视频` 链接，紧凑 post fallback 也保持相同映射。任一确认新增游戏缺少合法 YouTube 视频 URL 时，渲染会直接失败并报告游戏名，不允许静默发送无源结论，也不能只用商店链接代替视频源。

报告可用 `**Priority:** Focus` 显式指定重点；否则同日 2 个以上频道覆盖会自动进入候选重点。`--dry-run` 只在本地打印卡片预览，不会发送。

## 定时执行

`run_daily_once.sh` 使用原子目录锁和 `.run-stamps`，防止并发扫描及重复推送：

```bash
skills/game-scan-youtube/scripts/run_daily_once.sh
```

如果要创建 Codex 自动化，应让自动化调用此脚本。不要因为旧 Claude Code 任务或 cron 曾存在，就假设当前环境仍在正常运行；需要单独回读当前自动化状态。

完成戳不是由子进程退出码决定。只有以下证据全部成立才会写入：

- 当日报告存在、非空且可解析。
- `channels.json` 与 `seen_games.json` 是有效 JSON。
- 真实飞书发送生成 `receipts/YYYY-MM-DD.feishu.json`，其中包含 `message_id`。

手动排查时按以下顺序：

```bash
python3 skills/game-scan-youtube/scripts/collect_rss.py \
  --channels /path/to/workspace/channels.json \
  --output /path/to/workspace/scan-input-YYYY-MM-DD.json

python3 skills/game-scan-youtube/scripts/validate_run.py \
  --date YYYY-MM-DD \
  --dir /path/to/workspace
```

## 历史资产

- 18 份日期报告：`2026-05-12` 至 `2026-06-16`，日期不连续。
- 开发者调查：Markdown 与一批结构化 JSON 数据。
- 运行认知：项目记忆、预算/失败路由。
- 持续状态快照：`channels.json`、`seen_games.json`。

详细清单和来源边界见 `references/history/README.md`。

未迁入：

- Claude 原始会话 JSONL。
- `.omc` 会话状态。
- cron 原始日志、锁和完成戳。
- `.env` 或任何凭据。

这些不是扫描成果，且可能包含私人上下文、临时输出或敏感运行信息。

## 依赖

- Codex CLI；可选 Claude Code CLI。
- Python 3.9+。
- `curl`。
- 若要推送飞书，需要具备 IM 发消息权限的 Feishu/Lark 应用。

## 已知边界

- YouTube RSS、页面抓取和搜索源都可能出现 `403`、`429` 或 `5xx`。
- 连续失败时要降级并明确报告证据缺口，不要循环重试，也不要把 cron 启动当成扫描完成。
- `seen_games.json` 是去重真源；未确认的新视频不得写成已验证发现。
- 嵌套 Codex/Claude 不假设具备网络；RSS 必须先落成本地 `scan-input-*.json`。
