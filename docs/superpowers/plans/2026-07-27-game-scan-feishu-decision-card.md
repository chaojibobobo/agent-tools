# Game Scan Feishu Decision Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the long Feishu post digest with one compact decision card that answers “is today worth attention?” and “which game matters most, and why?” in the first screen.

**Architecture:** Keep Markdown parsing and scan completion gates unchanged. Add a focused `decision_card.py` presentation module that ranks parsed games, derives compact decision copy, and builds both the primary interactive card and a compact post fallback; keep network delivery and receipt persistence in `push_feishu.py`.

**Tech Stack:** Python 3.9 standard library, Feishu IM `interactive` message JSON, `unittest`, existing Markdown parser and receipt validator.

## Global Constraints

- The canonical mutable workspace remains `/Users/bobo/Codexspace/tools/game-scan-youtube`.
- The local Markdown report remains the full evidence source.
- The normal card body is at most 500 visible characters and 15 visible text lines.
- Expand one focus game by default and never more than two.
- Never send or edit the already-published 2026-07-27 Feishu message during implementation.
- Card fallback is allowed only before any `message_id` is received and may execute once.
- Report validation, state commit, real receipt validation, and done-stamp semantics remain unchanged.
- Use Python standard library only; add no runtime dependency.

---

### Task 1: Decision Model and Ranking

**Files:**
- Create: `skills/game-scan-youtube/scripts/decision_card.py`
- Modify: `skills/game-scan-youtube/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `parsed: dict` returned by `push_feishu.parse_report()`.
- Produces: `build_decision_model(parsed: dict) -> dict`.
- Model keys: `focus`, `supplemental`, `watchlist`, `updates`, `headline`, `conclusion`, `trend`, `counts`.

- [ ] **Step 1: Write failing ranking and compression tests**

Add a `DecisionCardModelTests` class using a fixture with one explicit Focus game, one four-channel implicit focus, one Track game, one watchlist item, and one known-game update:

```python
def decision_game(
    name, hook, *, priority="", cross_ref="1 channel",
    videos=None, watchlist=False, update=False, gameplay="",
):
    return {
        "name": name,
        "hook": hook,
        "is_update": update,
        "is_watchlist": watchlist,
        "videos": videos or [],
        "status": "Early Access",
        "developer": "Fixture Dev",
        "platform": "Android",
        "gameplay": gameplay,
        "download_url": "https://play.google.com/fixture",
        "store_signal": "new test",
        "cross_ref": cross_ref,
        "priority": priority,
    }


def decision_fixture():
    long_videos = [
        {
            "title": f"Gameplay {index}",
            "url": f"https://www.youtube.com/watch?v=focus{index}",
            "meta": f"Channel {index} · {10 + index}:15 · 100 views",
        }
        for index in range(1, 4)
    ]
    return {
        "is_quiet": False,
        "takeaways": [
            "成熟 IP 正在把局内 build 接回可见的局外资产。",
            "小体量策略正在减少操作，放大不可逆取舍。",
        ],
        "games": [
            decision_game(
                "Explicit Focus",
                "成熟 IP 用 Roguelike TD + 轻基地扩展局外成长",
                priority="Focus",
                cross_ref="3 channels",
                videos=long_videos,
            ),
            decision_game(
                "Implicit Focus",
                "four-channel strategy launch",
                cross_ref="4 channels",
                videos=[long_videos[0]],
            ),
            decision_game(
                "Track Game",
                "single-channel TD",
                priority="Track",
                videos=[long_videos[0]],
                gameplay="low gameplay detail should be compacted",
            ),
            decision_game(
                "Watch Candidate",
                "duration unverified",
                watchlist=True,
                videos=[long_videos[0]],
            ),
            decision_game(
                "Known Game",
                "update",
                update=True,
                videos=[long_videos[0]],
            ),
        ],
    }


class DecisionCardModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.card = load_script_module("decision_card")

    def test_model_answers_value_then_priority(self):
        parsed = decision_fixture()
        model = self.card.build_decision_model(parsed)

        self.assertEqual("今日值得看｜Explicit Focus", model["headline"])
        self.assertEqual(["Explicit Focus"], [g["name"] for g in model["focus"]])
        self.assertEqual(
            ["Implicit Focus", "Track Game"],
            [g["name"] for g in model["supplemental"]],
        )
        self.assertEqual("1 必看 · 2 补充 · 1 观察 · 1 更新", model["counts"])
        self.assertLessEqual(len(model["conclusion"]), 80)
        self.assertLessEqual(len(model["trend"]), 60)

    def test_focus_evidence_prefers_cross_channel_and_duration(self):
        model = self.card.build_decision_model(decision_fixture())
        focus = model["focus"][0]

        self.assertEqual("3 频道同时覆盖 · 3 条视频全部 ≥10 分钟", focus["why"])
        self.assertEqual("Roguelike TD → 轻基地扩展局外成长", focus["mechanism"])

    def test_two_explicit_focus_games_expand_but_never_more_than_two(self):
        parsed = decision_fixture()
        parsed["games"][1]["priority"] = "Focus"
        parsed["games"][2]["priority"] = "Focus"

        model = self.card.build_decision_model(parsed)

        self.assertEqual(2, len(model["focus"]))
        self.assertEqual(
            ["Implicit Focus", "Explicit Focus"],
            [game["name"] for game in model["focus"]],
        )
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
python3 -m unittest discover \
  -s skills/game-scan-youtube/tests \
  -p 'test_*.py' -v
```

Expected: the new model tests FAIL because `decision_card.py` and `build_decision_model()` do not exist; the existing tests remain PASS.

- [ ] **Step 3: Implement deterministic ranking helpers**

Create `decision_card.py` with these public and private functions:

```python
import re
from typing import Optional


def _cross_ref_count(game: dict) -> int:
    match = re.search(r"\d+", game.get("cross_ref", ""))
    return int(match.group()) if match else 0


def _is_explicit_focus(game: dict) -> bool:
    return game.get("priority", "").strip().lower() in {
        "focus", "must watch", "p0", "p1", "重点",
    }


def _is_explicit_track(game: dict) -> bool:
    return game.get("priority", "").strip().lower() in {
        "track", "watch", "p2", "补充",
    }


def _clip(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip("，。； ") + "…"


def _duration_seconds(meta: str) -> Optional[int]:
    match = re.search(r"(?:^|·\s*)(\d{1,2}):(\d{2})(?:\s*·|$)", meta or "")
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _mechanism(game: dict) -> str:
    hook = game.get("hook", "")
    if "用" in hook:
        hook = hook.split("用", 1)[1]
    hook = re.sub(r"\s*\+\s*", " → ", hook)
    return _clip(hook or game.get("gameplay", "") or game.get("status", ""), 44)


def _why(game: dict) -> str:
    videos = game.get("videos", [])
    count = _cross_ref_count(game)
    durations = [_duration_seconds(video.get("meta", "")) for video in videos]
    parts = []
    if count >= 2:
        parts.append(f"{count} 频道同时覆盖")
    elif game.get("status"):
        parts.append(game["status"])
    if videos:
        if durations and all(value is not None and value >= 600 for value in durations):
            parts.append(f"{len(videos)} 条视频全部 ≥10 分钟")
        else:
            parts.append(f"{len(videos)} 条完整 gameplay")
    if not parts and game.get("store_signal"):
        parts.append(_clip(game["store_signal"], 34))
    return " · ".join(parts[:2])
```

Implement `build_decision_model(parsed)` with the following exact policy:

```python
def build_decision_model(parsed: dict) -> dict:
    games = parsed.get("games", [])
    confirmed = [
        game for game in games
        if not game.get("is_update") and not game.get("is_watchlist")
    ]
    watchlist = [game for game in games if game.get("is_watchlist")]
    updates = [game for game in games if game.get("is_update")]

    ranked = sorted(
        confirmed,
        key=lambda game: (
            _is_explicit_focus(game),
            _cross_ref_count(game),
            len(game.get("videos", [])),
        ),
        reverse=True,
    )
    focus_candidates = [
        game for game in ranked
        if _is_explicit_focus(game)
        or (_cross_ref_count(game) >= 2 and not _is_explicit_track(game))
    ]
    explicit_count = sum(_is_explicit_focus(game) for game in focus_candidates)
    focus_limit = 2 if explicit_count >= 2 else 1
    focus_raw = focus_candidates[:focus_limit]
    focus_ids = {id(game) for game in focus_raw}
    supplemental_raw = [game for game in ranked if id(game) not in focus_ids]

    focus = [
        {**game, "why": _why(game), "mechanism": _mechanism(game)}
        for game in focus_raw
    ]
    takeaways = parsed.get("takeaways", [])
    if focus:
        conclusion = _clip(takeaways[0] if takeaways else focus[0]["hook"], 80)
        headline = f"今日值得看｜{focus[0]['name']}"
    elif confirmed:
        conclusion = "今天有新增，但没有形成多频道或明确强信号。"
        headline = f"今日有新增｜{len(confirmed)} 款"
    else:
        conclusion = "今天没有值得立即展开的新项目。"
        headline = "今日无重点"

    trend = _clip(takeaways[1] if len(takeaways) > 1 else "", 60)
    count_parts = []
    for count, label in (
        (len(focus), "必看"),
        (len(supplemental_raw), "补充"),
        (len(watchlist), "观察"),
        (len(updates), "更新"),
    ):
        if count:
            count_parts.append(f"{count} {label}")

    return {
        "focus": focus,
        "supplemental": supplemental_raw,
        "watchlist": watchlist,
        "updates": updates,
        "headline": headline,
        "conclusion": conclusion,
        "trend": trend,
        "counts": " · ".join(count_parts),
    }
```

- [ ] **Step 4: Run focused tests**

Run the command from Step 2.

Expected: PASS for both model tests.

- [ ] **Step 5: Commit the model**

```bash
git add skills/game-scan-youtube/scripts/decision_card.py \
  skills/game-scan-youtube/tests/test_pipeline.py
git commit -m "feat: model game scan decision summary"
```

---

### Task 2: Interactive Card and Compact Post Rendering

**Files:**
- Modify: `skills/game-scan-youtube/scripts/decision_card.py`
- Modify: `skills/game-scan-youtube/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `build_decision_model(parsed)`.
- Produces:
  - `build_interactive_card(report_date, parsed, summary) -> dict`
  - `build_compact_post(report_date, parsed, summary) -> dict`
  - `visible_card_text(card: dict) -> str`
  - `visible_post_text(post: dict) -> str`

- [ ] **Step 1: Write failing card schema and budget tests**

```python
def test_interactive_card_has_decision_header_tags_and_actions(self):
    card = self.card.build_interactive_card(
        date(2026, 7, 27),
        decision_fixture(),
        {"period": "", "total_games": 0, "total_channels": 50, "hot_games": []},
    )
    header = card["header"]
    text = self.card.visible_card_text(card)

    self.assertEqual("orange", header["template"])
    self.assertEqual("今日值得看｜Explicit Focus", header["title"]["content"])
    self.assertEqual(
        ["1 必看", "2 补充", "1 观察"],
        [tag["text"]["content"] for tag in header["text_tag_list"]],
    )
    self.assertIn("🔥 必看｜Explicit Focus", text)
    self.assertIn("为什么看：", text)
    self.assertIn("核心机制：", text)
    self.assertNotIn("low gameplay detail should be compacted", text)
    self.assertLessEqual(len(text), 500)
    self.assertLessEqual(len(text.splitlines()), 15)

    actions = [
        element for element in card["elements"] if element.get("tag") == "action"
    ]
    self.assertEqual("看视频", actions[0]["actions"][0]["text"]["content"])


def test_quiet_day_card_is_grey_and_has_no_focus(self):
    parsed = {"is_quiet": True, "takeaways": [], "games": []}
    card = self.card.build_interactive_card(
        date(2026, 7, 27),
        parsed,
        {"period": "07-20 ~ 07-27", "total_games": 3,
         "total_channels": 50, "hot_games": []},
    )
    self.assertEqual("grey", card["header"]["template"])
    self.assertEqual("今日无重点｜Quiet Day", card["header"]["title"]["content"])
    self.assertNotIn("必看", self.card.visible_card_text(card))
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
python3 -m unittest discover \
  -s skills/game-scan-youtube/tests \
  -p 'test_*.py' -v
```

Expected: the new rendering tests FAIL because the rendering functions do not exist.

- [ ] **Step 3: Implement the interactive card**

Use Feishu card v1-compatible elements:

```python
def _tag(content: str, color: str) -> dict:
    return {
        "tag": "text_tag",
        "text": {"tag": "plain_text", "content": content},
        "color": color,
    }


def _markdown(content: str) -> dict:
    return {
        "tag": "div",
        "text": {"tag": "lark_md", "content": content},
    }


def _button(label: str, url: str, primary: bool = False) -> dict:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": "primary" if primary else "default",
        "url": url,
    }
```

For a normal day, render elements in this order:

```python
elements = [
    _markdown(f"**结论**\n{model['conclusion']}"),
    {"tag": "hr"},
]
```

For the first focus game:

```python
elements.append(_markdown(
    f"**🔥 必看｜{focus['name']}**\n"
    f"为什么看：{focus['why']}\n"
    f"核心机制：{focus['mechanism']}"
))
```

Add one `action` element containing at most one video button and one store button. Then append:

```python
if model["supplemental"]:
    supplemental = "\n".join(
        f"• {game['name']}｜{_clip(game.get('hook') or game.get('status'), 32)}"
        for game in model["supplemental"][:4]
    )
    elements.append(_markdown(f"**➕ 补充**\n{supplemental}"))

if model["trend"]:
    elements.append(_markdown(f"**趋势一句话**\n{model['trend']}"))

if model["watchlist"] or model["updates"]:
    labels = []
    if model["watchlist"]:
        labels.append(f"观察 {len(model['watchlist'])} 款")
    if model["updates"]:
        labels.append(f"更新 {len(model['updates'])} 款")
    elements.append(_markdown("**⚪ " + " · ".join(labels) + "**"))
```

Header tags include at most three non-zero counts in the order `必看、补充、观察`; updates move into the body if the three-tag cap is reached.

- [ ] **Step 4: Implement compact post fallback with the same hierarchy**

`build_compact_post()` returns the existing `zh_cn.title/content` shape but uses only:

```text
结论｜...
🔥 必看｜...
为什么看｜...
核心机制｜...
➕ 补充｜Game A；Game B
趋势｜...
⚪ 观察 N 款 · 更新 N 款
```

Do not include full `gameplay`, `store_signal`, or three takeaways.

Keep `push_feishu.build_feishu_content()` as a compatibility wrapper that delegates to `build_compact_post()`. Rewrite the existing `test_multichannel_findings_lead_and_single_channel_findings_are_compact` assertions from the v2 headings to the v3 compact-post headings and budget; do not delete its signal-ranking coverage.

- [ ] **Step 5: Add visible text helpers and run tests**

`visible_card_text()` traverses header title/tags, `div.text.content`, and button labels. `visible_post_text()` traverses the current rich-post `content` matrix.

Run:

```bash
python3 -m unittest discover \
  -s skills/game-scan-youtube/tests \
  -p 'test_*.py' -v
```

Expected: PASS, with visible card text ≤500 characters and ≤15 lines.

- [ ] **Step 6: Commit rendering**

```bash
git add skills/game-scan-youtube/scripts/decision_card.py \
  skills/game-scan-youtube/tests/test_pipeline.py
git commit -m "feat: render compact Feishu decision card"
```

---

### Task 3: Interactive Delivery, Single Fallback, and Receipt Format

**Files:**
- Modify: `skills/game-scan-youtube/scripts/push_feishu.py`
- Modify: `skills/game-scan-youtube/tests/test_pipeline.py`

**Interfaces:**
- Consumes: card and compact post from Task 2.
- Produces:
  - `push_report(card_content, fallback_content, dry_run=False, request_fn=post_json, token_fn=get_token) -> dict`
  - Result keys: `status`, `message_id`, `message_format`, `fallback_used`.

- [ ] **Step 1: Write failing delivery tests**

```python
def test_card_failure_before_message_id_falls_back_once(self):
    calls = []

    def fake_post(url, payload, headers=None, query=None):
        calls.append(payload["msg_type"])
        if payload["msg_type"] == "interactive":
            return {"code": 230099, "msg": "invalid card"}
        return {"code": 0, "data": {"message_id": "om_fallback"}}

    result = self.push_feishu.push_report(
        {"header": {}, "elements": []},
        {"zh_cn": {"title": "fallback", "content": []}},
        request_fn=fake_post,
        token_fn=lambda: "fixture-token",
    )

    self.assertEqual(["interactive", "post"], calls)
    self.assertEqual("om_fallback", result["message_id"])
    self.assertEqual("post", result["message_format"])
    self.assertTrue(result["fallback_used"])


def test_successful_card_never_sends_fallback(self):
    calls = []

    def fake_post(url, payload, headers=None, query=None):
        calls.append(payload["msg_type"])
        return {"code": 0, "data": {"message_id": "om_card"}}

    result = self.push_feishu.push_report(
        {"header": {}, "elements": []},
        {"zh_cn": {"title": "fallback", "content": []}},
        request_fn=fake_post,
        token_fn=lambda: "fixture-token",
    )

    self.assertEqual(["interactive"], calls)
    self.assertEqual("interactive", result["message_format"])
    self.assertFalse(result["fallback_used"])
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
python3 -m unittest discover \
  -s skills/game-scan-youtube/tests \
  -p 'test_*.py' -v
```

Expected: FAIL because current `push_report()` accepts only one post payload.

- [ ] **Step 3: Import the sibling presentation module safely**

At the top of `push_feishu.py`, add its script directory to `sys.path` before import so direct execution and `importlib` tests both work:

```python
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from decision_card import (
    build_compact_post,
    build_interactive_card,
    visible_card_text,
)
```

- [ ] **Step 4: Implement primary card send and one fallback**

Change `push_report()` to accept both payloads and an injectable request function:

```python
def push_report(
    card_content,
    fallback_content,
    dry_run=False,
    request_fn=post_json,
    token_fn=get_token,
):
    if dry_run:
        print("[DRY RUN] Type: interactive")
        print(visible_card_text(card_content))
        return {
            "status": "dry-run",
            "message_id": None,
            "message_format": "interactive",
            "fallback_used": False,
        }

    token = token_fn()
    common = {
        "headers": {"Authorization": f"Bearer {token}"},
        "query": {"receive_id_type": "open_id"},
    }
    card_data = request_fn(
        f"{BASE_URL}/im/v1/messages",
        {
            "receive_id": os.environ["FEISHU_USER_OPEN_ID"],
            "msg_type": "interactive",
            "content": json.dumps(card_content),
        },
        **common,
    )
    if card_data.get("code") == 0:
        return {
            "status": "sent",
            "message_id": card_data["data"]["message_id"],
            "message_format": "interactive",
            "fallback_used": False,
        }

    unexpected_message_id = (card_data.get("data") or {}).get("message_id")
    if unexpected_message_id:
        raise RuntimeError(
            "card returned a message_id with a failure code; refusing fallback"
        )

    fallback_data = request_fn(
        f"{BASE_URL}/im/v1/messages",
        {
            "receive_id": os.environ["FEISHU_USER_OPEN_ID"],
            "msg_type": "post",
            "content": json.dumps(fallback_content),
        },
        **common,
    )
    if fallback_data.get("code") != 0:
        raise RuntimeError(
            f"card failed: {card_data}; fallback failed: {fallback_data}"
        )
    return {
        "status": "sent",
        "message_id": fallback_data["data"]["message_id"],
        "message_format": "post",
        "fallback_used": True,
    }
```

Do not call fallback after a card response containing a successful `message_id`.

- [ ] **Step 5: Update main, dry-run, and receipt**

Build both payloads:

```python
card_content = build_interactive_card(report_date, parsed, summary)
fallback_content = build_compact_post(report_date, parsed, summary)
push_result = push_report(
    card_content,
    fallback_content,
    dry_run=args.dry_run,
)
```

The dry-run must print a readable card preview and never call Feishu. Add these receipt fields without removing existing fields:

```python
"message_format": push_result["message_format"],
"fallback_used": push_result["fallback_used"],
```

- [ ] **Step 6: Run delivery and full regression tests**

Run:

```bash
python3 -m unittest discover \
  -s skills/game-scan-youtube/tests \
  -p 'test_*.py' -v
```

Expected: all original 9 tests plus new decision-card tests PASS.

- [ ] **Step 7: Commit delivery**

```bash
git add skills/game-scan-youtube/scripts/push_feishu.py \
  skills/game-scan-youtube/tests/test_pipeline.py
git commit -m "feat: deliver game scan as Feishu card"
```

---

### Task 4: Skill Contract, Static Compliance, and Local Replay

**Files:**
- Modify: `skills/game-scan-youtube/SKILL.md`
- Modify: `skills/game-scan-youtube/README.md`
- Test: `skills/game-scan-youtube/tests/test_pipeline.py`
- Sync: `/Users/bobo/.codex/skills/game-scan-youtube/`

**Interfaces:**
- Consumes: Task 3 card builder and sender.
- Produces: documented Decision Card v3 behavior and a synchronized installed skill.

- [ ] **Step 1: Add frontmatter metadata required by static checks**

Add to `SKILL.md` frontmatter:

```yaml
argument-hint: "[YYYY-MM-DD] [report-only|push]"
user-invocable: true
```

Keep the current `name`, `description`, `metadata`, and `allowed-tools`.

- [ ] **Step 2: Replace the Feishu output contract**

Update `Output expression` and `IM Push` to specify:

```text
价值结论
→ 必看（默认 1 款，最多 2 款）
→ 为什么看（1 条最强证据）
→ 核心机制（最多 3 个节点）
→ 补充（每款 1 行）
→ 趋势一句话
→ 观察与更新（只显示数量或最多 2 个名称）
```

Document header color semantics and the 500-character/15-line budgets. State that the full Markdown report remains the evidence layer.

- [ ] **Step 3: Update README examples and file inventory**

Add `decision_card.py` to the directory tree. Replace the old four-layer post description with Decision Card v3 and explain that `--dry-run` previews the card without sending.

- [ ] **Step 4: Run structural and behavior validation**

Run:

```bash
python3 /Users/bobo/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skills/game-scan-youtube

python3 -m unittest discover \
  -s skills/game-scan-youtube/tests \
  -p 'test_*.py' -v
```

Expected:

- `Skill is valid!`
- All tests PASS.
- Static manual check improves from 1 failure / 1 warning to 0 failures / 0 warnings.

- [ ] **Step 5: Replay 2026-07-27 locally without sending**

Run:

```bash
python3 skills/game-scan-youtube/scripts/push_feishu.py \
  --date 2026-07-27 \
  --dir /Users/bobo/Codexspace/tools/game-scan-youtube \
  --dry-run
```

Expected preview:

```text
今日值得看｜Soul Knight Defense
[1 必看] [2 补充] [1 观察]
```

The preview must directly show Soul Knight Defense before supplemental games, omit the three full takeaways, stay within the defined budget, and perform no network send.

- [ ] **Step 6: Install and compare**

Run:

```bash
scripts/install-skill.sh game-scan-youtube
diff -qr \
  skills/game-scan-youtube \
  /Users/bobo/.codex/skills/game-scan-youtube
```

Expected: installation succeeds and `diff -qr` prints no differences.

- [ ] **Step 7: Commit docs and contract**

```bash
git add skills/game-scan-youtube/SKILL.md \
  skills/game-scan-youtube/README.md
git commit -m "docs: define game scan decision card contract"
```

---

### Task 5: Final Verification and Handoff

**Files:**
- Modify: `/Users/bobo/Codexspace/tools/_better-me-handoffs/2026-07-27-game-scan-youtube-manual-recovery.md`

**Interfaces:**
- Consumes: all prior task outputs.
- Produces: final evidence summary and reusable decision-card acceptance rule.

- [ ] **Step 1: Run the complete verification set**

```bash
python3 -m py_compile \
  skills/game-scan-youtube/scripts/decision_card.py \
  skills/game-scan-youtube/scripts/push_feishu.py

python3 -m unittest discover \
  -s skills/game-scan-youtube/tests \
  -p 'test_*.py' -v

python3 /Users/bobo/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skills/game-scan-youtube

diff -qr \
  skills/game-scan-youtube \
  /Users/bobo/.codex/skills/game-scan-youtube
```

Expected: compilation succeeds, all tests PASS, skill validation passes, and source/install diff is empty.

- [ ] **Step 2: Confirm no production resend**

Read `/Users/bobo/Codexspace/tools/game-scan-youtube/receipts/2026-07-27.feishu.json` and verify its `message_id` is unchanged from the completed scan. Do not run a non-dry-run push.

- [ ] **Step 3: Update the handoff**

Record:

- old replay size: 1539 characters / 25 lines;
- new replay size and line count;
- card header, tags, and first-focus project;
- test total and validator result;
- source/install diff result;
- explicit statement that 2026-07-27 was not re-sent.

- [ ] **Step 4: Commit the handoff only if repository policy permits**

`_better-me-handoffs/` is ignored by repository policy, so do not force-add it. Report the local path in the final response.
