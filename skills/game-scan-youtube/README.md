# Game Scan YouTube

发现处于早期测试的移动 4X SLG。YouTube 频道雷达找一手实机，不按国家筛选；商店雷达定期巡检可配置的重点市场，也接收其他地区有一手证据的上架线索。只有实机通过 `4X-SLG-STRICT-v1` 品类校验，才可列为确认新品。

频道权重只评价严格 4X 命中、领先发现、独有发现和视频证据质量；目标国家覆盖不影响频道权重。频道可达性与本轮覆盖单独记录为 `source_health`。遇到断网或覆盖不足，运行器保留游标、只发运维告警，下次重采。配置、字段和离线演练见[运行手册](docs/operations.md)。

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
│   ├── youtube_transport.py
│   ├── run_health.py
│   ├── signal_funnel.py
│   ├── prepare_product_radar.py
│   ├── intelligence_state.py
│   ├── market_scope.py
│   ├── validate_run.py
│   ├── commit_state.py
│   ├── run_scan.sh
│   ├── run_daily_once.sh
│   ├── decision_card.py
│   └── push_feishu.py
├── assets/bootstrap/
│   ├── channels.json
│   ├── seen_games.json
│   ├── candidate_history.json
│   ├── product_radar.json
│   └── scan_state.json
└── references/history/
    ├── README.md
    └── reports/
```

新运行会产生：

- `scan-input-YYYY-MM-DD.json`：L0 探针、分层覆盖、视频时间精度与运行状态。
- `product-radar-input-YYYY-MM-DD.json`：本轮重点市场清单与各区的独立搜索任务。
- `candidate-ledger-YYYY-MM-DD.json`：Core / Pending / Reject / Product Lead 的结构化判断账本。
- `candidate_history.json`：含 Reject 在内的长期判断记忆。
- `product_radar.json`：重点市场巡检状态（`checked` / `failed` / `not_run`）、各地区去重后的上架观察及其后续实机线索。
- `scan_state.json`：仅成功提交后推进的扫描游标。

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
/Users/bobo/Codexspace/tools/game-scan-youtube
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

## 商店雷达查哪些市场？

默认重点市场是马来西亚、印度尼西亚、菲律宾、英国、泰国和加拿大。**重点名单决定每轮主动查哪里，不决定哪里发现的游戏能入账。**

在持续工作区新建 `radar-markets.json` 可长期增减：写入**完整替换名单**，至少保留一个市场，使用不重复的两字母地区代码。例如，今后只巡检马来西亚、菲律宾和日本：

```json
{"focus_markets": ["MY", "PH", "JP"]}
```

只改一次，可给 `run_scan.sh` 或生成清单的 `prepare_product_radar.py` 加 `--markets JP,GB`。命令行优先于工作区配置，不改配置文件；两者都不设置才使用默认名单。

例如，若本轮只巡检日本和英国，却发现美国区 Google Play 的具体游戏详情页：核实包名、地区和可用状态后，可记录美国区上架观察，但不把美国计入本轮巡检覆盖，也不会自动加入下轮名单。只有商店证据、尚无一手实机时，留作 `Product Lead`；国家和地区不参与 YouTube 频道评分。

## 使用

交互使用时，可以直接告诉 Codex：

```text
运行 game-scan-youtube，扫描今天的新移动 4X SLG 实机，并巡检我配置的重点市场。
```

交互式手动扫描由当前 Codex 直接按 skill 阶段执行，不再启动嵌套的 `codex exec`。只有明确需要非交互 runner 时才使用下面脚本；它会先在父进程采集 RSS，再让独立 agent 分析本地证据：

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
  --dir /Users/bobo/Codexspace/tools/game-scan-youtube \
  --dry-run
```

真实推送：

```bash
python3 skills/game-scan-youtube/scripts/push_feishu.py \
  --date YYYY-MM-DD \
  --dir /Users/bobo/Codexspace/tools/game-scan-youtube
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

聊天卡片可见文案硬限 500 字、15 行。标题先给结论，观察与更新最多两行；本地 Markdown 保留完整依据和逐款原片。达到历史报告预算上限时，dry-run 会标明只读原视频索引，而非可发送卡片。

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

触发 `RUN_DEGRADED` 熔断时，只发运维告警（无飞书凭据则只留本地记录），不发日报、不推进扫描游标，也不写入 `.run-stamps` 完成戳。下次定时任务再执行该日期时会重新采集。

手动排查时按以下顺序：

```bash
python3 skills/game-scan-youtube/scripts/collect_rss.py \
  --channels /path/to/workspace/channels.json \
  --output /path/to/workspace/scan-input-YYYY-MM-DD.json

python3 skills/game-scan-youtube/scripts/validate_run.py \
  --date YYYY-MM-DD \
  --dir /path/to/workspace \
  --require-intelligence-ledger
```

## 历史资产

- 18 份日期报告：`2026-05-12` 至 `2026-06-16`，日期不连续。
- 持续状态快照：`channels.json`、`seen_games.json`、`candidate_history.json`、`product_radar.json`、`scan_state.json`。

详细清单和来源边界见 `references/history/README.md`。

未迁入：

- Claude 原始会话 JSONL。
- `.omc` 会话状态。
- cron 原始日志、锁和完成戳。
- `.env` 或任何凭据。
- 含个人工作上下文的 operations 记录。
- 含第三方联系邮箱的开发者调查原稿。

这些不是公开 skill 的必要运行资产，且可能包含私人上下文、第三方联系方式、临时输出或敏感运行信息。本地副本可以继续保留，但不进入公开远端。

## 依赖

- Codex CLI；可选 Claude Code CLI。
- Python 3.9+。
- `curl`。
- `yt-dlp`（可选，推荐安装）：RSS 被限流或超时时用于读取频道上传列表。若装在自定义位置，可在环境变量或工作区 `.env` 中设置 `YTDLP_BIN` 为可执行文件的绝对路径。
- `YOUTUBE_API_KEY`（可选）：配置后优先启用 L1 官方上传列表通道（`playlistItems.list`）；不配置则从 RSS 开始采集。
- 若要推送飞书，需要具备 IM 发消息权限的 Feishu/Lark 应用。

## 已知边界

- 有 Key 优先上传列表 API；RSS 失败按条件退到本机 yt-dlp，再到频道页，相对时间不得冒充准确上传时间。L0 失败、有效或优先频道覆盖未达阈值时停止常规日报闭环。
- 重点市场覆盖优先看当地 Google Play，Apple 用于核对 iOS 和日期。非重点地区要有能指向同款游戏、同一地区的官方商店商品证据；搜索结果、转载或其他地区的页面不能证明该区上架。
- 连续失败时要降级并明确报告证据缺口，不要循环重试，也不要把 cron 启动当成扫描完成。
- `seen_games.json` 是去重真源；未确认的新视频不得写成已验证发现。
- 嵌套 Codex/Claude 不假设具备网络；RSS 必须先落成本地 `scan-input-*.json`。
