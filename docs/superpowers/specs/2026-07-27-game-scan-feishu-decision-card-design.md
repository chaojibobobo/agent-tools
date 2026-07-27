# Game Scan 飞书决策卡片设计

## 日期

2026-07-27

## Context

`game-scan-youtube` 当前使用飞书富文本消息发送每日摘要。现有 v2 已经按“今日判断 → 先看 → 其他新增 → 观察与更新”区分信号强弱，但 2026-07-27 回放仍有 1539 字、25 行。聊天框第一屏先出现三段长判断，用户不能快速回答两个核心问题：

1. 今天是否值得看？
2. 最值得看哪款，为什么？

本地 Markdown 报告继续承担完整证据；飞书消息只承担每日决策入口。

## User request

- 使用 `A+B` 决策目标：先判断今天值不值得看，再指出最值得看哪款及原因。
- 标题和重点信息需要特殊呈现。
- 用户批准方案 A：单条飞书决策卡片。

## Goals

- 第一屏直接呈现当日价值判断和第一优先项目。
- 用标题颜色、标题标签、图标、短字段和按钮建立稳定视觉层级。
- 正常日正文控制在约 500 个中文字符、15 个可见文本行以内。
- 只展开 1 款最强项目；确有两个同等级强信号时最多展开 2 款。
- 保留当前去重、报告校验、状态提交、回执校验和 done stamp 门禁。

## Non-goals

- 不缩减本地 Markdown 报告的完整证据。
- 不改变 RSS 采集、品类筛选、状态提交和完成门槛。
- 不在本次修改中发送或编辑已经发布的 2026-07-27 消息。
- 不拆成摘要与详情两条消息。

## Recommended presentation

### Header

标题直接表达价值判断，不再使用泛化标题：

```text
今日值得看｜Soul Knight Defense
```

标题标签最多三个：

```text
[1 必看] [2 补充] [1 观察]
```

标题颜色语义：

| 状态 | 颜色 | 使用条件 |
|---|---|---|
| 有明确重点 | orange | 至少 1 款 Focus 或多频道强信号 |
| 只有普通新增 | blue | 有确认新增，但没有重点 |
| Quiet Day | grey | 无新视频或新游戏 |
| 扫描失败 | red | 仅用于独立失败通知，不得冒充日报 |

### Body hierarchy

正常日固定为：

```text
结论
有强信号。优先看成熟 IP 如何用“肉鸽塔防 + 轻基地”
建立局内战斗与局外所有感。

🔥 必看｜Soul Knight Defense
为什么看：3 个频道同时覆盖，3 条视频均超过 10 分钟
核心机制：Roguelike TD → Boss 招募 → Knight Home
[看视频] [看商店]

➕ 补充
Some of You May Die｜自由强化 Auto Battler
Lucky Survival｜三选一 + 12 格背包

趋势一句话
小体量策略正在减少操作，放大不可逆取舍。

⚪ 观察 1 款
```

信息规则：

- `结论`：从 `What They Are Playing` 中抽取或压缩为最多 2 句、80 个中文字符。
- `必看`：按 `Priority: Focus` 优先，其次按同日频道覆盖数排序。
- `为什么看`：只保留最强证据，优先顺序为多频道覆盖、视频时长、测试/发行状态、商店增长信号。
- `核心机制`：将玩法描述压成最多 3 个节点的机制链，使用 `→` 连接；不得复制完整 `What you'll see`。
- `补充`：每款只保留 `名称｜一句机制标签`，不在正文重复频道数、状态和商店数据。
- `趋势一句话`：只保留一条跨项目共同判断。
- `观察与更新`：只显示数量；有必要时最多列出 2 个名称，不展开机制。
- 链接使用卡片按钮或明确的可点击文本，不把裸 URL 放入正文。

## Data flow

```text
Markdown report
  → parse_report()
  → rank focus / supplemental / watchlist / updates
  → derive decision headline
  → derive evidence line
  → derive mechanism chain
  → build interactive card JSON
  → Feishu send
  → message receipt
```

现有 `parse_report()` 继续作为结构化入口。展示层只消费已解析字段：

- `name`
- `hook`
- `priority`
- `status`
- `gameplay`
- `store_signal`
- `cross_ref`
- `videos`
- `download_url`
- `takeaways`

不从聊天卡片反向修改报告或状态。

## Delivery and fallback

- 主发送类型从 `post` 改为 `interactive` 消息卡片。
- 卡片发送成功并返回 `message_id` 后，沿用现有 receipt 写入和最终校验。
- 如果卡片请求在获得 `message_id` 前因结构不兼容而失败，允许在同一次任务中回退到紧凑富文本版本。
- 回退版本保持同一信息顺序和字符预算，不恢复当前 1539 字长摘要。
- 只有没有收到任何 `message_id` 时才能回退，防止重复发送。
- 回退发生时在 receipt 中记录实际发送格式，便于后续排查。

## Implementation boundary

预计修改：

- `skills/game-scan-youtube/scripts/push_feishu.py`
  - 增加决策摘要压缩函数。
  - 增加 interactive card builder。
  - 保留 compact post fallback。
  - receipt 增加实际消息格式字段。
- `skills/game-scan-youtube/tests/test_pipeline.py`
  - 增加卡片结构、排序、字符预算和回退测试。
- `skills/game-scan-youtube/SKILL.md`
  - 把飞书输出规范更新为“决策卡片 v3”。
  - 补齐静态检查缺失的 frontmatter 元数据。
- 安装副本 `/Users/bobo/.codex/skills/game-scan-youtube`
  - 测试通过后使用仓库安装脚本同步。

## Tests and acceptance criteria

### Behavioral tests

- 有 Focus 时，标题必须包含“今日值得看”和第一重点名称。
- 第一重点必须出现在补充项之前。
- 默认只展开 1 个重点，最多 2 个。
- 重点必须包含且只包含一条“为什么看”和一条“核心机制”。
- 补充项每款只占一行。
- 趋势最多一条。
- 观察项不能计入确认新增或重点数量。
- 正常日可见正文不超过 500 个中文字符、15 个文本行。
- 卡片链接必须包含至少一个视频入口；有商店链接时显示商店入口。
- Quiet Day 使用灰色标题，不伪造重点。
- 卡片失败且无 `message_id` 时只回退一次；收到 `message_id` 后禁止回退。

### Regression gates

- 现有 9 项行为测试全部继续通过。
- `quick_validate.py` 通过。
- 源码与安装副本同步后 `diff -qr` 一致。
- 使用 2026-07-27 报告 dry-run 回放，目标结果：
  - `1 必看 · 2 补充 · 1 观察`
  - 第一屏直接出现 Soul Knight Defense
  - 不再完整复述三条 `What They Are Playing`
  - 不重复发送真实飞书消息

## Final judgment

`APPROVED DESIGN`

方案 A 能直接解决聊天框的决策抓取问题，并保留本地报告作为完整证据层。实现时应优先保证单条卡片的第一屏可读性，而不是继续向卡片中增加信息。

## Reuse

以后评估每日情报推送时，先用两个问题验收：第一屏能否判断“今天值不值得看”，以及能否指出“最值得看哪款、为什么”。如果不能，即使信息完整也不算可用摘要。
