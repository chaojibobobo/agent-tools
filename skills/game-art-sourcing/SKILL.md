---
name: game-art-sourcing
description: Use when the user names a game, franchise, scene, or gameplay video and asks for game-art research, visual references, press kits, sprites, models, UI assets, art-style analysis, frame-by-frame analysis, 拉片, 美术参考, or a Feishu-ready art report. Do not use for gameplay-system teardown, ad creative analysis, market/revenue research, or reviewing one supplied UI screenshot without external art research.
argument-hint: "[game name or gameplay video URL] [art-research request]"
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash
---

# Game Art Sourcing

把游戏名或 gameplay 视频转成有来源、有画面证据、可落地的游戏美术研究。核心原则是：先建立 source manifest，再做视觉判断；每个技术结论都绑定图片、关键帧或明确来源。

## 30 秒美术定调卡

报告顶部先给这张卡；没有通过本地质检的图片或真实提色数据时，字段写 `证据不足`，不得补全幻觉：

```text
视觉一句话：
风格基底词：3–6 个可观察词
主色 / 辅色 / 点缀色：#HEX（近似占比，来自本地代表图量化）
角色证据：图 ID / 本地路径
场景证据：图 ID / 本地路径
UI/实机证据：图 ID / 本地路径
最值得复用或警惕：
图源门禁：READY / CONCERNS / BLOCKED
```

## 触发边界

Positive Triggers：游戏美术调研、角色/场景/UI/VFX 视觉参考、官方素材与 press kit、Art Bible 证据研究、gameplay 视频美术拉片、带图飞书美术报告。

Negative Triggers：

- 玩法系统、数值、关卡机制或商业化拆解：改用相应 game-design / systems skill。
- 买量广告、广告创意和投放素材分析：不触发本 skill。
- 市场规模、营收、榜单、发行表现或竞品商业研究：不触发本 skill。
- 只审一张用户已提供的 UI 截图且不需要外部取证：改用 `game-ui-review`。
- “调研”“素材”两个泛词本身不构成触发；必须明确是游戏美术或视觉资产问题。

## 自动组合

内部判断任务需要哪些输出，不要让用户选择模式，也不要把内部路由展示成菜单或选择题：

- 核心输出始终保留：视觉一句话、1–3 条关键证据、最值得复用或警惕的结论。
- 根据请求自动追加相关模块；与任务无关的模块不输出，没有证据的相关模块明确写 gap。
- 复杂请求可以叠加多个模块，不要求用户切换“完整”或“复杂”模式。
- 给出 YouTube/Bilibili 链接，或要求逐帧分析、拉片时，完整读取并执行 `subskills/video-frame-art-analysis/SKILL.md`；以视频帧为主证据，只在验证观察或补充缺失视角时增加外部素材。
- 游戏名检索在 L1 + L2 后仍少于 8 张有效图，或角色/场景/UI 实机任一核心桶为 0 时，也正式路由到 `subskills/video-frame-art-analysis/SKILL.md` 的 L3 自动兜底；依赖或视频不可用时回到父流程，保留图源残缺门禁。
- 只审查一张现成 UI 的层级、可读性、一致性或本地化风险，且不要求外部美术研究时，改用 `game-ui-review`。

可自动追加的模块包括素材与来源、逐帧拉片、竞品比较、Visual System、Art Bible 规则、生产可行性、AI prompt 和飞书交付。生成报告前按 `references/report-spec.md` 的选择规则组合，禁止为了套模板而补齐无关模块。

只有完整分析、系统性研究、当前项目借鉴、Art Bible 或生产可行性请求才完整读取 `references/visual-system-stack.md`，按五层框架展开有证据的相关层。简单素材请求不要求完整读取该 reference；单点问题也只使用相关层。每个展开层必须回答玩家情绪、功能可读性、进程或状态变化。

## 运行时根目录

命令统一通过可覆盖的根目录运行，避免绑定某个 agent runtime：

```bash
GAME_ART_SKILL_ROOT="${GAME_ART_SKILL_ROOT:-${CODEX_SKILLS_DIR:-$HOME/.codex/skills}/game-art-sourcing}"
test -f "$GAME_ART_SKILL_ROOT/SKILL.md"
```

从源码仓库直接调试时：

```bash
GAME_ART_SKILL_ROOT="$(git rev-parse --show-toplevel)/skills/game-art-sourcing"
```

## 权限与外部写入

- 在当前 runtime 已授予的权限内连续执行；这份 skill 本身不扩大文件、网络、浏览器或凭证权限。
- 单张图片的 `404/403` 可以跳过并寻找替代，但要在最终报告中暴露素材覆盖缺口。
- 发布飞书属于外部写入。只有用户明确要求飞书交付，或已约定完成标准包含飞书文档时才发布。
- 凭证只从 `--config`、`GAME_ART_CONFIG` 或 `~/.config/game-art-sourcing/config.yaml` 读取；不要输出、记录或写入仓库。
- 默认不授予个人账号文档权限；仅在用户要求且配置了 `user_open_id` 时使用 `--grant-user-access`。
- 若需要读取本地浏览器 cookie，先取得用户对该浏览器 profile 的明确授权。

## 主流程

只显示实际执行的步骤；数字和文案可随选中的模块调整，未请求飞书时不显示发布步骤：

```text
[1/6] 正在检查缓存与输入...
[2/6] 正在建立官方来源清单...
[3/6] 正在收集 gameplay 与视觉素材...
[4/6] 正在拆解角色、场景、UI 与技法...
[5/6] 正在生成并本地校验报告...
[6/6] 正在发布并验收飞书文档...
```

### 1. 输入与缓存

为游戏名生成 URL-safe slug，将临时报告隔离在 `/tmp/game-art-<slug>-*`。

检查 7 天缓存：

```bash
python3 "$GAME_ART_SKILL_ROOT/cache_manager.py" read "Game Name"
```

缓存命中时仍要检查来源日期和用户的新重点；旧缓存不能覆盖新要求。

### 2. 建立 source manifest

按以下优先级收集并记录标题、URL、来源类型和用途：

1. 游戏官网、开发商、发行商、官方 press kit。
2. Steam/App Store/Google Play 商店页与官方 CDN 截图。
3. 官方 trailer、gameplay、开发者日志和美术幕后。
4. ArtStation 作者页、Sketchfab 作者页、官方 Wiki。
5. 社区资料只作补充，不能替代官方事实。

按请求选择相关证据；完整分析通常收集：

- 开发商、发行年份、平台、可验证的视觉标签。
- 封面和 3–5 张有代表性的商店截图。
- 请求涉及的角色、场景、UI/VFX 等证据素材；找不到时明确写 gap。
- 3–5 个高价值视频或幕后链接；没有结果时不要编造。

所有搜索型资源链接都带具体关键词，避免只交裸域名首页。图片 URL 不可确认可访问时，改用来源页面链接。

### 2.1 分桶配额与三级图源

默认用 `scripts/image_sourcing.py` 收集并把通过质检的图片落盘；总目标 8–14 张，参数可调：

- `key_art`：1–2 张。
- `character`：3–4 张。
- `environment`：3–4 张。
- `ui_gameplay`：2–3 张。

按层级补缺，不因为找到一张 `og:image` 就停止：

1. L1：Steam Store Search + `appdetails` 的 `header_image` / 全量 `path_full`；iTunes Search API 的 iPhone/iPad 截图；Google Play 商品页高清图。
2. L2：ArtStation 项目 JSON 与四层 CDN 回退、游戏官网/Press Kit/Fandom 页面完整正文图集；请求带匹配域名的 `Referer` 和合理 `User-Agent`。
3. L3：总有效图少于 8 张，或 `character` / `environment` / `ui_gameplay` 任一为 0 时，用视频子技能的 `yt-dlp + ffmpeg` 抽帧兜底。

```bash
python3 "$GAME_ART_SKILL_ROOT/scripts/image_sourcing.py" "Game Name" \
  --platform auto \
  --output-dir "/tmp/game-art-game-name-images" \
  --manifest "/tmp/game-art-game-name-research-data.json"
```

分桶只是候选组织，不是视觉结论。商店 API 和视频轮转得到的 `quota_balance_requires_review` 图片必须由模型逐张读取本地文件后确认或改桶。

### 2.2 本地洗图与真实提色

分析前必须读取 manifest 中实际落盘的本地图片，而不是只看 URL、文件名或来源文案：

- 过滤宽 < 480px、高 < 270px、极端横竖比、近纯色/坏图。
- 用 SHA-256 + dHash 去除同文件和跨分辨率近重复图。
- 将 WebP/AVIF/异常编码标准化为飞书兼容 JPG/PNG；超大图等比缩放并压缩到上传门限内。
- 对角色/场景代表图做 Pillow 色彩量化，排除近纯黑/纯白边框；报告只可引用 manifest 已记录的真实 Hex 与近似占比。
- 每个角色、场景、UI 核心结论紧邻对应分桶图片或图 ID；禁止先写结论再补无关图。

### 3. 美术拆解

只展开请求相关的对象；用户要求完整分析时，按画面证据覆盖：

- **角色**：头身比、轮廓、服装密度、材质对比、色彩角色、面部和动作可读性。
- **场景**：空间尺度、前中后景、建筑/道具密度、镜头高度、冷暖、气氛和视觉引导。
- **UI/VFX**：字体、图标、边缘处理、透明度、屏幕位置、色彩编码、特效形状与覆盖率。
- **技法**：PBR/NPR、贴图、材质、描边、Bloom、Color Grading、SSAO、DOF、体积光等。

分析技法前读取 `references/art-technique-framework.md`。无法确认时用“疑似、可能、证据不足”，不要从单帧强推实现技术。

### 4. 形成可复用结论

核心输出始终包含视觉一句话、1–3 条关键证据以及最值得复用或警惕的结论。追加分析或项目模块时，每个重要结论包含：

1. 观察到的事实。
2. 图片、时间戳或来源证据。
3. 对美术方向的含义。
4. 可直接复用 / 适配后复用 / 不应复制。
5. 生产成本、风险和小团队实现建议。

AI Prompt 仅在用户明确要求时输出，并使用固定结构：

```text
风格基底词 + [主体替换位] + Negative Prompt
```

风格基底词只能来自已确认的形状、材质、光照、镜头和真实色板；`[主体替换位]` 保留为可编辑槽位；Negative Prompt 明确排除与证据冲突的风格、材质、构图和 UI 噪声。

## 报告生成

生成 JSON 前完整读取 `references/report-spec.md`。需要处理大量独立材料时，可在当前 runtime 与用户授权允许的前提下，把“资料归纳”和“JSON 结构校验”拆成独立任务；主任务保留来源审计、发布和验收责任。

报告路径：

```text
/tmp/game-art-<slug>-research-data.json
/tmp/game-art-<slug>-report.json
/tmp/game-art-<slug>-publish-status.json
```

写入缓存：

```bash
python3 "$GAME_ART_SKILL_ROOT/cache_manager.py" write \
  "Game Name" \
  "/tmp/game-art-<slug>-research-data.json"
```

发布前先做无网络、无凭证的本地转换校验：

```bash
python3 "$GAME_ART_SKILL_ROOT/scripts/publish_to_feishu.py" \
  "/tmp/game-art-<slug>-report.json" \
  --validate-only
```

校验失败时修复 JSON，不创建飞书文档。

## 飞书发布与验收

用户要求飞书交付时：

```bash
python3 "$GAME_ART_SKILL_ROOT/scripts/publish_to_feishu.py" \
  "/tmp/game-art-<slug>-report.json" \
  --title "Game Name — 美术调研报告"
```

需要给配置中的用户显式授权时：

```bash
python3 "$GAME_ART_SKILL_ROOT/scripts/publish_to_feishu.py" \
  "/tmp/game-art-<slug>-report.json" \
  --title "Game Name — 美术调研报告" \
  --grant-user-access
```

完成门禁：

- 命令返回文档 URL。
- publish status 为 `done`。
- 脚本回读确认文档至少包含一个实际内容 block。
- 抽查标题、首图和至少一个正文图片；图片不可见或用户无权访问时，先修复再交付，不反复生成无用文档。
- 单张上传失败先重试 1 次，再从同桶备用候选顶替；所有候选失败时删除空图片块，不得静默留白。
- 实际回读图片少于 6 张，或角色/场景/UI 任一核心桶为 0 时，文档顶部必须出现 `【图源残缺：...】`，终端必须输出 URL、分桶入图回执、剔除/失败原因三行。

## 常见错误

- 先写风格结论、后补来源。
- 把剧情简介当美术分析。
- 同一图片在多个章节重复使用。
- 从压缩缩略图断言具体 shader。
- 给出 ArtStation/Sketchfab 裸首页而不是带关键词或具体作品链接。
- 本地校验未通过就创建飞书文档。
- 把“文档创建成功”当成图片、权限和内容都已验收。
- 只统计下载成功，不回读飞书图片 token。
- 用模型凭空编 Hex 色板，或未查看本地图片就写角色/场景/UI 结论。

## 历史与兼容

历史来源、迁移边界、旧代码和原仓库提交脉络在 `references/history/`。这些文件用于审计，不是当前执行入口；当前执行以本文、视频子技能和 `scripts/publish_to_feishu.py` 为准。

## 后续动作

任务完成后给出 `READY / CONCERNS / BLOCKED` 图源门禁、实际分桶数量和证据 gap；需要飞书时再执行发布与回读，不把本地报告自动外发。
