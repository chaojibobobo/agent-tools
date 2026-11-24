# 运行与排查

在技能源目录下执行示例；`W=/Users/bobo/Codexspace/tools/game-scan-youtube` 是持续工作区，`D` 为报告日期。临时演练不指向正式目录写文件。

## 配置与采集

- Python 3.9+、curl；可选 yt-dlp、`YOUTUBE_API_KEY`。环境变量优先；否则读取显式 `--env` / `GAME_SCAN_ENV_FILE`，或工作区 `.env`，再找 `~/.game-scan-youtube/.env`。保留文件为仅用户可读，不要提交。代理可用 `HTTPS_PROXY` / `ALL_PROXY` / `HTTP_PROXY`。预检只打印布尔配置，不打印值。
- 默认 5 个并发线程；`--workers` 可调整，`--timeout` 限制单频道总耗时，`--retries 1|2` 控制 RSS 退避。无 Key 不影响运行，有 Key 优先 [playlistItems.list](https://developers.google.com/youtube/data/reference/rest/v3/playlistItems/list)（每次 1 单位配额，UC → UU），不用 search.list。视频时间取 `contentDetails.videoPublishedAt`，不是加入播放列表的时间；额外时长/观看量查询仅对未见过的 ID 批量执行，`--no-enrich-details` 可关闭。
- L2 复用工作区 `rss_http_cache.json` 的 ETag、Last-Modified 和内容；304 用当前窗口重筛，缓存不等于已发布。403、429、5xx、超时依次尝试本机 yt-dlp 的 flat playlist 和频道页。L0 探针失败不会启动频道线程。不要为绕过限流反复调用同一源。
- 新采集仍写 schema 2 和 `source_summary` / `channel_results` / `videos`。可选顶层 `run_status` 区分 RUN_OK / RUN_DEGRADED；`source_health` 提供 active/succeeded/failed_channels、healthy_rate、healthy_threshold、probe_status、fetch_tier_breakdown、degraded_reason。分层数字为最终成功的**频道数**，不是重试次数。旧 `source_summary.status=degraded` 可表示成功降级，不等于熔断；已有计数可复算，旧 blocked 仍阻断。`--health-threshold 0.6` 或 `GAME_SCAN_HEALTH_THRESHOLD` 可配置。

仅环境自检（一次真实网络探针，不采集、不推送）：`python3 scripts/collect_rss.py --channels "$W/channels.json" --preflight-only`。

全量采集：`python3 scripts/collect_rss.py --channels "$W/channels.json" --state "$W/scan_state.json" --output "$W/scan-input-$D.json"`。采集器返回 1 时先查落地的状态；runner 对 RUN_DEGRADED 返回 **75**，保留单独的 `degraded-YYYY-MM-DD.md`，下次重新采集。运维告警不会生成普通日报回执。

## 用临时目录模拟断网

下面两步不依赖外网，不会改正式游标或使用飞书凭据。故意指定临时目录里不存在的 fixture 目录，使 L0 失败。

```bash
T=$(mktemp -d)
python3 scripts/collect_rss.py --channels assets/bootstrap/channels.json --feed-dir "$T/no-feeds" --output "$T/scan-input-2099-01-01.json"
# 上条预期退出 1，并已写出 RUN_DEGRADED。继续预览三行告警：
python3 scripts/push_feishu.py --dir "$T" --date 2099-01-01 --ops-alert --dry-run
```

## 报告与账本接口

报告用 `> **Scan Profile:** 4X-SLG-STRICT-v1`。在报告顶部写以下**独立行**；趋势没有足够样本时省略后两行，脚本自动输出固定兜底。下例只展示格式，两款产品必须确实在当日或前六天出现，且有证据支持同一现象。

```markdown
> **Card Headline:** 【值得看】首次看到矿车跨区运矿
> **Card Trend:** 两款都把矿车护送接入领土争夺
> **Trend Products:** 产品甲｜产品乙
```

每个发布项目置于对应 `##` 分区并用 `### 游戏名 — 本次增量` 开头；原片独占一行 `- [视频标题](该款的 YouTube 直链) — 频道 · 时长`。发布报告保留 `**4X Fit:**` 与 `**4X Evidence:**`。以下文案键用半角冒号。

| 分区 | 额外字段示例 |
|---|---|
| New Games，必看 | `**Priority:** Focus`、`**Card Evidence:** 08:14 矿车占领联盟矿点`、`**Card Mechanism:** 造车→运矿→守矿` |
| New Games，补充 | `**Priority:** Track`、`**Card Form:** 护送+领土4X`、`**Card Signal:** 两频道同日实测` |
| 待核实发现（时间存疑） | `**Time Status:** unverified`，不写 HH:MM，只描述待证事项。不把这条记成已发布新品 |
| New Videos for Known Games | 原片、具体更新；更名时账本候选填 `renamed_from`（seen_games 旧键）与 `rename_evidence`（具体特征），正文标注原名及依据 |

新输入走严格文案校验；旧 schema 不要求回写新字段。历史报告过长时，历史 dry-run 可以明确输出不可发送的原视频索引，而不是发一张超限卡片。缺原片的历史发现仍拒绝。报告的长篇研究正文不占卡片预算。

## 商店覆盖与上架线索分别记账

- `product_radar_run.countries` 只记录本轮清单中的重点市场，每个地区恰好一次。`checked` 要有对应地区 Google Play 来源；只有 Apple 可达记 `failed`，未执行记 `not_run`。检查完一个商店搜索页，不等于验证其中每款都已上架。
- `candidates[].store_observations` 可以记录其他市场。例如本轮只查 JP、GB，却拿到美国区的某款游戏：用当地 Google Play 商品详情（对应包 ID 和 `gl=US`）或美国 App Store 商品详情（`apps.apple.com/us/app/…/id…`）核实，保留商品标识、地区来源、观察时间及可用状态。第三方线索、商店搜索页和别区商品页不能作为名单外入账证据。这个 US 观察不计入巡检覆盖、不改变下次重点名单或频道权重；无一手实机只留 `Product Lead`。

## 验收、发送和历史边界

1. 准备本轮商店巡检清单：`python3 scripts/prepare_product_radar.py --date "$D" --output "$W/product-radar-input-$D.json"`。长期范围来自输出目录中的 `radar-markets.json`（`{"focus_markets":["MY","PH","JP"]}` 是完整替换，不是追加）；单次加 `--markets JP,GB` 可覆盖，不修改配置。同日验收以生成时的清单为准，即便后来改了工作区配置。
2. 本地编辑报告与账本；运行 `python3 scripts/validate_run.py --date "$D" --dir "$W" --require-intelligence-ledger`。预览用 `python3 scripts/push_feishu.py --date "$D" --dir "$W" --dry-run`。
3. 仅全量健康且已验证的运行可用 `commit_state.py --date "$D" --dir "$W"`。已授权发送且有 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_USER_OPEN_ID` 时，再调用 push 去掉 `--dry-run`。interactive 被拒且未拿到 message_id 才能尝试一次 compact post。验收加 `--require-push-receipt`；运维消息不能充当送达凭证。
4. `run_scan.sh --dry-run` **不是只读预览**：它会调用代理、生成报告并可能提交健康状态。仅看卡片用 push 的 dry-run。不得用单频道排查结果覆盖全量采集输入；不要因本地预览自动开启定时调度。

最初迁入的 18 份报告随 `references/history/reports/` 保持原文。来源与排除清单见[历史索引](../references/history/README.md)；`init_workspace.sh` 只复制缺失文件，支持 `--no-history`。真实报告、日志、凭据、缓存、收据和完成戳不进入技能源码。
