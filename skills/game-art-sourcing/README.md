# Game Art Sourcing

面向 Codex、Claude Code 和其他 agent runtime 的游戏美术研究 skill。输入游戏名或 gameplay 视频后，先按角色/场景/UI/主视觉分桶采图并完成本地质检，再组合 Visual System、证据覆盖和项目生产建议；按需发布到飞书。

默认图片目标为 8–14 张：`key_art` 1–2、`character` 3–4、`environment` 3–4、`ui_gameplay` 2–3。采集按 L1 官方商店结构化接口 → L2 作品集/官网完整图集 → L3 视频抽帧自动回退，避免手游、新品和未完整上架项目只有一两张图。

本目录于 2026-07-30 从旧工作副本迁入并适配 Codex。旧代码与历史认知保存在 `references/history/`，不作为当前运行入口。

## 安装到 Codex

在 tools 仓库根目录运行：

```bash
scripts/install-skill.sh game-art-sourcing
```

安装后下一轮 Codex 对话可自动发现。

## 依赖

- Python 3.9+
- `requests`
- `PyYAML`
- `Pillow`
- AVIF 兼容建议安装 `pillow-avif-plugin`（Pillow 构建已原生支持 AVIF 时可省略）
- 视频逐帧分析额外需要 `yt-dlp`、`ffmpeg`

安装 Python 依赖：

```bash
python3 -m pip install -r skills/game-art-sourcing/requirements.txt
```

视频工具：

```bash
brew install yt-dlp ffmpeg
```

## 飞书配置

凭证保存在 skill 目录之外，避免安装更新时被覆盖或误提交：

```bash
mkdir -p ~/.config/game-art-sourcing
cp skills/game-art-sourcing/config.example.yaml \
  ~/.config/game-art-sourcing/config.yaml
chmod 600 ~/.config/game-art-sourcing/config.yaml
```

也可以通过以下方式指定：

```bash
export GAME_ART_CONFIG=/secure/path/config.yaml
```

默认不会给 `user_open_id` 授予文档权限。只有明确使用 `--grant-user-access` 才会授予 `full_access`。

## 使用

无需选择“简单/完整/复杂模式”，直接说明想解决的问题。

只找素材：

```text
帮我找《South of Midnight》的官方 press kit、角色图和场景截图，只要可靠来源。
```

完整分析：

```text
完整分析《South of Midnight》的美术：角色、场景、材质、UI、VFX、镜头、玩家情绪和状态变化都要覆盖。
```

为当前项目提炼生产规则：

```text
研究《South of Midnight》，为我们当前项目提炼角色、场景、材质和 VFX 生产规则，标出成本、风险和最小验证动作。
```

视频拉片：

```text
把这个 YouTube gameplay 做一次逐帧美术拉片，提炼成新项目可复用规则。
```

所有有效任务都保留视觉一句话、1–3 条关键证据、最值得复用或警惕的结论；其他模块由 skill 根据请求自动追加。完整分析和项目型请求使用五层 Visual System，项目型重要建议进一步输出生产决策卡。

玩法拆解、买量广告素材分析、市场/营收研究不属于本 skill。单张 UI 截图的层级、可读性和多语言 Review 也应使用 `game-ui-review`。

## 图片采集与本地质检

Steam 游戏：

```bash
python3 skills/game-art-sourcing/scripts/image_sourcing.py \
  "Zombie Cure Lab" \
  --platform steam \
  --output-dir /tmp/game-art-zombie-cure-lab-images \
  --manifest /tmp/game-art-zombie-cure-lab-images.json
```

纯手游（iTunes Search API；可附加 Google Play 商品页）：

```bash
python3 skills/game-art-sourcing/scripts/image_sourcing.py \
  "Last War:Survival Game" \
  --platform mobile \
  --output-dir /tmp/game-art-last-war-mobile-images \
  --manifest /tmp/game-art-last-war-mobile-images.json
```

可追加 `--page-url`（官网、Press Kit、ArtStation、Fandom）、`--google-play-url`、`--video-url`，也可用多次 `--quota bucket=count` 调整目标；总目标不得超过 14。

manifest 记录：

- 四桶数量、L1/L2/L3 使用情况和同桶备用图。
- 每张本地标准化 JPG/PNG 的尺寸、SHA-256、dHash、来源与分类方式。
- 角色/场景代表图由 Pillow 量化得到的真实 Hex 和近似占比。
- 低清、极端比例、近纯色、坏编码、重复和下载失败的剔除原因。

模型必须逐张读取 manifest 中的本地图片，确认 `quota_balance_requires_review` 候选的实际分桶，再撰写角色、场景和 UI 结论。AI Prompt 固定使用 `风格基底词 + [主体替换位] + Negative Prompt`，且只在用户明确要求时输出。

单张 UI 截图的层级、可读性和多语言 Review 不属于本 skill，应使用 `game-ui-review`。

## 本地校验

报告 JSON 在发布前先做无网络、无凭证校验：

```bash
python3 skills/game-art-sourcing/scripts/publish_to_feishu.py \
  /path/to/report.json \
  --validate-only
```

运行 package tests：

```bash
python3 skills/game-art-sourcing/tests/test_skill_package.py
```

或运行全量测试：

```bash
python3 -m unittest discover -s skills/game-art-sourcing/tests -v
```

## 发布飞书

```bash
python3 skills/game-art-sourcing/scripts/publish_to_feishu.py \
  /path/to/report.json \
  --title "游戏名 — 美术调研报告"
```

发布器会先在本地标准化和去重。单图上传失败会重试一次，再尝试该图片块声明的同桶 `alternates`。最终入图少于 6 张，或角色/场景/UI 任一为 0 时，文档顶部插入 `【图源残缺：...】`；终端输出 URL、分桶入图/来源、剔除与失败原因三行回执，不再静默留下空图片块。

报告图片块可使用以下向后兼容字段：

```json
{
  "type": "image",
  "url": "/tmp/game-art/example/environment-01.jpg",
  "bucket": "environment",
  "source_tier": "L1",
  "source_label": "Steam",
  "alternates": [
    {"url": "/tmp/game-art/example/environment-backup.jpg", "bucket": "environment"}
  ],
  "caption": "E01 — 场景证据"
}
```

显式给配置中的用户授权：

```bash
python3 skills/game-art-sourcing/scripts/publish_to_feishu.py \
  /path/to/report.json \
  --title "游戏名 — 美术调研报告" \
  --grant-user-access
```

## 运行状态

- 搜索缓存默认在 `~/.cache/game-art-sourcing/`，可用 `GAME_ART_CACHE_DIR` 覆盖。
- 飞书配置默认在 `~/.config/game-art-sourcing/config.yaml`，可用 `GAME_ART_CONFIG` 或 `--config` 覆盖。
- 临时报告和 publish status 写入 `/tmp/game-art-*`。

## 目录

```text
game-art-sourcing/
├── SKILL.md
├── README.md
├── agents/openai.yaml
├── block_builder.py
├── cache_manager.py
├── feishu_client.py
├── config.example.yaml
├── requirements.txt
├── scripts/
│   ├── image_sourcing.py
│   └── publish_to_feishu.py
├── subskills/video-frame-art-analysis/SKILL.md
├── references/
│   ├── art-technique-framework.md
│   ├── report-spec.md
│   ├── visual-system-stack.md
│   └── history/
├── evals/evals.json
└── tests/
    ├── test_image_pipeline.py
    └── test_skill_package.py
```

## 明确未迁入

- 旧仓库 `.git/` 和 `.omc/` 会话状态。
- 旧 Claude runtime 的本机命令授权配置。
- 真实 `config.yaml`、缓存和任何凭证。
- `.pub`、临时文件和认证状态。

真实凭证只迁移到本机外部配置位置，不进入 tools 仓库或 skill 安装包。
