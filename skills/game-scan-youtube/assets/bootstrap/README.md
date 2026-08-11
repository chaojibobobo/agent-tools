# Bootstrap State

这些 JSON 用于初始化新的可写工作区。旧频道和游戏状态来自 2026-07-25 迁入快照，新增智能状态从空账本开始。

- `channels.json`：频道权重与健康度快照。
- `seen_games.json`：已报告游戏和视频 URL 去重库。
- `candidate_history.json`：Core / Pending / Reject / Product Lead 的结构化判断历史。
- `product_radar.json`：独立的 MY / ID / PH / GB / TH / CA 产品雷达。
- `scan_state.json`：最近一次成功提交的扫描游标。

运行时不要直接修改这里。使用 `scripts/init_workspace.sh` 把缺失文件复制到 `GAME_SCAN_WORK_DIR`；已有状态不会被覆盖。
