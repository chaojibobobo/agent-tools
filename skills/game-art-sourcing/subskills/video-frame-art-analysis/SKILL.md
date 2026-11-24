---
name: video-frame-art-analysis
description: Child workflow for game-art-sourcing when the input is a YouTube or Bilibili video URL. Use it to extract metadata, sample real key frames, bind observations to timestamps, and assemble only the evidence-backed analysis modules requested by the user.
---

# Video Frame Art Analysis

This child skill handles video-first game art research. Treat the provided video as the primary visual source, preserve timestamp evidence, and only turn observations into project rules when the user asks for project guidance.

## When To Use

Use this child workflow when the parent `game-art-sourcing` skill receives:

- A YouTube URL: `youtube.com/watch?v=...`, `youtu.be/...`, `youtube.com/shorts/...`
- A Bilibili URL: `bilibili.com/video/BV...`, `b23.tv/...`
- A user request for "逐帧分析", "拉片", "从视频分析美术风格", or "根据这个视频做游戏美术分析"
- Or the parent image pipeline reaches L3 because L1 + L2 produced fewer than 8 usable images, or `character` / `environment` / `ui_gameplay` has no usable image.

Two routing contexts are different:

- **Video-first request**: this workflow is the primary evidence path; missing dependencies block a formal video report.
- **Parent L3 fallback**: return extracted candidates to the parent's four buckets. If dependencies/video access fail, return an explicit L3 gap so the parent can publish only with `【图源残缺】`; do not erase valid L1/L2 evidence.

## Workflow

Use this progress shape:

```text
[1/7] 正在识别视频来源...
[2/7] 正在获取视频元数据...
[3/7] 正在检查抽帧依赖...
[4/7] 正在抽取关键帧...
[5/7] 正在筛选高价值画面...
[6/7] 正在逐帧标注并综合所需维度...
[7/7] 正在生成请求所需输出...
```

### Step 1: Identify Source

Extract:

- Platform: YouTube / Bilibili
- Canonical URL
- Video ID: YouTube id or Bilibili BV id
- Possible game name from title, description, tags, or visible metadata

Use a filesystem-safe slug based on the video ID:

```bash
SLUG=$(python3 -c "import base64;print(base64.urlsafe_b64encode('VIDEO_ID'.strip().lower().encode()).decode().rstrip('=')[:80])")
```

Use these paths:

- Frame directory: `/tmp/game-art-${SLUG}-frames/`
- Research data: `/tmp/game-art-${SLUG}-video-data.json`
- Report JSON, only when the selected output requires JSON: `/tmp/game-art-${SLUG}-report.json`
- Progress JSON: `/tmp/game-art-${SLUG}-gen-progress.json`

### Step 2: Collect Metadata

Collect, when available:

- Title
- Channel / UP 主
- Duration
- Publish date
- Thumbnail / cover URL
- Description summary
- Whether the video is official trailer, gameplay capture, review, cutscene, or mixed edit

Prefer official platform metadata. For YouTube, oEmbed is acceptable for title/channel confirmation. For Bilibili, preserve BV id and page URL even if deeper metadata is blocked.

### Step 3: Dependency Preflight

Before frame extraction, check for the required local tools:

```bash
YTDLP="${GAME_ART_YTDLP:-$(command -v yt-dlp 2>/dev/null)}"
FFMPEG="${GAME_ART_FFMPEG:-$(command -v ffmpeg 2>/dev/null)}"
test -x "$YTDLP" || YTDLP=/opt/homebrew/bin/yt-dlp
test -x "$YTDLP" || YTDLP=/usr/local/bin/yt-dlp
test -x "$FFMPEG" || FFMPEG=/opt/homebrew/bin/ffmpeg
test -x "$FFMPEG" || FFMPEG=/usr/local/bin/ffmpeg
test -x "$YTDLP" || echo "MISSING: yt-dlp"
test -x "$FFMPEG" || echo "MISSING: ffmpeg"
```

Required roles:

- `yt-dlp`: downloads or probes YouTube/Bilibili video streams.
- `ffmpeg`: extracts timestamped frames and creates scan sheets.

If either tool is missing:

1. Do **not** silently claim full frame-by-frame analysis.
2. Output a dependency block with exact install and verification commands:
   ```bash
   brew install yt-dlp ffmpeg
   yt-dlp --version
   ffmpeg -version
   ```
3. For a video-first request, stop before formal report generation unless the user explicitly asks for a `thumbnail-only smoke test`.
4. For parent L3 fallback, return `L3 unavailable` with the missing dependency; the parent keeps valid L1/L2 evidence and applies its incomplete-image gate.
5. Only after that explicit request, use thumbnails/cover or official screenshots and mark every visual conclusion as `evidence limited（证据有限）`.

For restricted Bilibili or age/login-gated YouTube videos, first try public metadata. If download requires login state, use:

```bash
yt-dlp --cookies-from-browser chrome "VIDEO_URL"
```

Only use browser cookies when the user has approved access to their local browser profile.

### Step 4: Extract Frames

Prefer real frames over thumbnails. Use the best available method in the runtime:

1. If `yt-dlp` and `ffmpeg` are available, download/probe the video and extract real frames.
2. If download is blocked but browser playback is available, use browser screenshots at timestamps.
3. If only thumbnail/cover is accessible, stop formal report generation. Continue only after the user explicitly requests a `thumbnail-only smoke test`, and mark the result throughout as `evidence limited（证据有限）`.

Recommended extraction pattern:

```bash
mkdir -p /tmp/game-art-${SLUG}-frames
yt-dlp -f "bv*[height<=1080]+ba/b[height<=1080]/b" -o "/tmp/game-art-${SLUG}.%(ext)s" "VIDEO_URL"
ffmpeg -hide_banner -i "/tmp/game-art-${SLUG}.mp4" -vf "fps=1/6,scale=1280:-1" "/tmp/game-art-${SLUG}-frames/scan_%05d.jpg"
```

For automatic parent fallback, prefer the packaged entry point so tool discovery, black/pure-color rejection, local normalization, dedupe and bucket manifest stay consistent:

```bash
python3 "$GAME_ART_SKILL_ROOT/scripts/image_sourcing.py" "Game Name" \
  --video-url "VIDEO_URL" \
  --output-dir "/tmp/game-art-game-name-images"
```

If the downloaded extension is not `.mp4`, use the actual output file path from `yt-dlp`.

Sampling strategy:

- **Full-video scan**: sample every 5-8 seconds to map visual variety.
- **Shot keyframes**: capture 1-3 representative frames per major scene change.
- **Micro-action pass**: for combat, VFX bursts, UI popups, traversal, animation beats, or transitions, sample every 0.25-0.5 seconds.

Keep frame filenames timestamped:

```text
/tmp/game-art-${SLUG}-frames/000012.50_scene-forest.jpg
/tmp/game-art-${SLUG}-frames/000045.25_vfx-impact.jpg
```

### Step 5: Select High-Value Frames

Reject:

- Black frames, logos, loading screens, heavy subtitle obstruction
- Near-duplicates
- Frames with severe motion blur unless blur is itself the style evidence
- Pure transition frames with low art information

The automated pass must skip the first seconds, reject low-entropy/near-solid frames, and visually review remaining candidates for black frames and pure Logo/title cards before they enter a bucket. Do not count near-duplicate frames as separate evidence.

Keep the categories needed by the user's request:

- Hero/character clear views
- Environment establishing shots
- Gameplay readability frames
- VFX peak frames
- UI/HUD/menu frames
- Strong composition frames
- Lighting/color palette shifts
- Frames that reveal material or rendering technique

For a full pull-sheet or complete analysis, target 12-30 analyzed frames for a normal trailer and 30-60 for long gameplay videos. For a single-dimension request, select the smallest representative set that supports the requested conclusion; do not fill unrelated categories.

### Step 6: Frame Annotation Schema

For each selected frame, record:

```json
{
  "timestamp": "00:45.25",
  "frame_path": "/tmp/game-art-xxx-frames/000045.25_vfx-impact.jpg",
  "segment_type": "official_gameplay",
  "shot_type": "combat / environment / character / ui / vfx / transition",
  "screen_content": "画面内容一句话",
  "evidence_coverage": {
    "source_type": "官方 trailer",
    "game_state": "战斗",
    "time_weather": "夜晚/雨",
    "shot_distance": "中景",
    "progress_stage": "未确认"
  },
  "observations": {
    "composition": "主体位置、视线引导、前中后景",
    "color": "主色/辅色/强调色、冷暖、饱和度、Hex 推测"
  },
  "confidence": "high / medium / low"
}
```

`timestamp`、`frame_path`、`segment_type`、`shot_type`、`screen_content`、五轴 `evidence_coverage` 和 `confidence` 是基础字段。`observations` 只加入请求点名或结论直接需要的维度，可选键包括 `composition`、`color`、`lighting`、`shape_language`、`material_rendering`、`vfx_ui`、`animation_note`；不要为单维请求填满全套字段。

只有当前项目借鉴、Art Bible 或生产可行性请求才可额外加入 `actionable_takeaway`，且它只是进入生产决策卡的候选观察，不能代替完整决策卡。

`segment_type` 使用固定值：

- `real_gameplay_capture`：有连续游玩、HUD 或输入反馈证据的真实 gameplay 录屏，来源是否官方由 `source_type` 另行记录。
- `official_gameplay`：官方明确标注为 gameplay、但经过剪辑或无法从该片段独立确认连续游玩的演示。
- `cutscene`：过场或叙事演出。
- `pure_promotional`：Logo、标题卡、离线宣传镜头或不代表游玩的纯营销片段。
- `mixed_or_unknown`：混合剪辑或无法可靠判断。

`evidence_coverage` 五个字段必须直接使用 `references/report-spec.md` 的父五轴枚举与记录规则，不自行缩写或另造等价值。`source_type` 记录来源载体，`segment_type` 记录该帧在视频中的内容类型：当 `source_type` 是 `官方 trailer` 时，每帧仍必须区分 `real_gameplay_capture`、`official_gameplay`、`cutscene`、`pure_promotional` 或 `mixed_or_unknown`。

Every technical claim needs visual evidence. Use "疑似/可能" when evidence is incomplete.

每张关键帧都映射到父 skill 定义的五维证据覆盖矩阵：

```text
来源类型 × 游戏状态 × 时间/天气 × 镜头距离 × 进度阶段
```

轴值与记录规则以 `references/report-spec.md` 为准。无法确认的轴写 `未确认`，不能从装备华丽程度或宣传文案猜测进度。连续帧若来自同一镜头，视为一个证据簇，不当作多份独立支持。

完成标注后生成单视频覆盖摘要：

- 列出五轴实际覆盖的取值、独立镜头/证据簇数量。
- 明确未出现的关键游戏状态、时间天气、镜头距离和进度阶段。
- 明确单视频天然缺少跨来源验证；同一发布者、同一剪辑或同一录制账号不能证明全游戏规律。
- trailer 必须区分 gameplay、过场和纯宣传片段；Key Art、封面或商店截图只能补品牌证据，不能填补真实 gameplay 缺口。
- 若单视频只覆盖一个状态、时间天气、镜头距离或未知进度，主动缩小结论范围并降低相关置信度。

### Step 7: Synthesize Requested Dimensions

Every valid video analysis keeps only the minimum synthesis required by the parent core output:

- **视觉一句话**：one precise sentence limited to observable features.
- **1–3 条关键证据**：timestamped frames or accessible source evidence.
- **最值得复用或警惕的结论**：state what the evidence supports and does not support.

Beyond that minimum, only synthesize dimensions explicitly named or directly required by the request. For example, a color-and-camera request aggregates only color and camera evidence; it does not automatically add character, environment, rendering, VFX, UI, motion or production rules.

Only a complete-analysis request synthesizes every relevant dimension or Visual System layer that has evidence. Relevant but unsupported dimensions become explicit gaps; unrelated dimensions remain absent. Do not generate a full Style DNA checklist merely because frames were extracted.

Do not automatically translate a video into a new-game brief. Project rules, Art Bible guidance and production feasibility are added only for project-facing requests and use production decision cards in Step 8.

### Step 8: Assemble Adaptive Output

不要沿用旧的固定七章节报告。保留视频证据基础，其他模块只按用户请求追加。

Required foundation for every valid video analysis:

1. **Core output** from `references/report-spec.md`: 视觉一句话、1–3 条关键证据、最值得复用或警惕的结论。
2. **视频来源与抽帧说明**: platform, canonical URL, title, channel/UP, duration, video type, extraction method, frame count, limitations and fallback status.
3. **时间戳证据与覆盖**: selected frame paths, timestamp evidence, visual observations, five-axis coordinates, coverage summary and single-video gaps.

其他模块按需追加：

| User request | Append |
| --- | --- |
| 逐帧、镜头、色彩、材质、UI 或 VFX | Relevant keyframe annotations and only the requested Style DNA / technique sections |
| 完整分析、系统研究 | Relevant Visual System layers, full coverage summary, Style DNA and supported technique analysis |
| 当前项目借鉴、Art Bible、生产可行性 | Relevant Visual System layers plus one production decision card per important recommendation |
| Explicit AI prompt / 生成参考 request | English prompts grounded in verified shape, color, material, lighting and camera rules |
| Explicit Feishu delivery request | Feishu-ready JSON, local validation, publish and readback acceptance |

项目借鉴必须生成生产决策卡，并逐条使用父规范的固定链路：

```text
观察 → 证据与覆盖 → 置信度 → 可转移规则 → 项目适配 → 成本/风险 → 最小验证动作
```

In every card, separate `事实` from `推测`. Bind evidence to frame timestamps and five-axis coordinates. The minimum validation action must be the smallest executable test of the largest uncertainty, with a target camera/state, comparison variable, and pass/fail signal.

Only append AI prompt when the user explicitly asks for a prompt or generated-reference prompt. A request for analysis, project guidance or a moodboard does not implicitly authorize prompt generation.

Only publish to Feishu when the user explicitly requests Feishu delivery or the already-agreed completion standard names Feishu. A video URL, JSON report, project-guidance request or the existence of Feishu credentials does not implicitly authorize publishing.

### Step 9: JSON And Feishu Safety

When the selected output requires JSON, use the same `{"title": "...", "blocks": [...]}` schema as the parent skill. Local frame paths are allowed in image blocks if the publish script supports local images.

- For local JSON without Feishu delivery, generate the requested blocks and run `publish_to_feishu.py --validate-only`; do not publish.
- For explicit Feishu delivery, validate locally before creating a document. On validation failure, fix JSON and do not create a document.
- Publish status must be `done`, then read back the title, first image and at least one body image. Confirm content and access are visible before delivery.
- Prefer local frame files over expiring signed CDN URLs. If an image upload fails or rate limits, repair/back off and complete one usable document instead of repeatedly creating broken documents.
- Keep credentials outside report JSON and output. Never print config contents, tokens, cookies or browser login state.
- Browser cookies remain opt-in as defined in dependency preflight; Feishu user access also requires the parent's explicit grant behavior.

For a local/Feishu keyframe path summary, use pipe-delimited lines:

```text
timestamp | frame path | source URL | five-axis coordinates | evidence note
```

Image caption format:

```text
00:45.25 战斗爆发帧 — 主体被青绿色 VFX 包围，暗背景压低饱和度，技能边缘使用高亮描边保证读招清晰；疑似 Bloom + additive particle。
```

## Quality Rules

- Bind every conclusion to a timestamp or frame.
- Keep video source, frame-extraction method and timestamp evidence as the non-optional foundation; all other analysis modules are adaptive.
- Do not write long story summaries unless they explain visual direction.
- Do not overclaim technology from one frame.
- If the video is a cinematic trailer, clearly distinguish cinematic art direction from gameplay-readable art direction.
- If subtitles or compression obscure details, say so.
- Prefer a smaller set of strong frames over many weak frames.
- For project-facing requests, decision cards must be useful for art direction, Art Bible rules, production feasibility and the next validation action.

## Fallbacks

- If required dependencies are missing or real-frame extraction fails, stop formal report generation and report the exact blocker. Do not replace a formal frame analysis with thumbnails or official screenshots.
- Only when the user explicitly requests a `thumbnail-only smoke test` may accessible thumbnail/cover frames and official screenshots be used. Label the whole result and every visual conclusion `evidence limited（证据有限）`; state that it has no valid timestamp-frame evidence and is not a formal video report.
- If the source video is not a game video, stop with a concise explanation and do not fabricate a game art report.
- If the game name is unknown, title the report with the video title and include `游戏名：未确认`.
