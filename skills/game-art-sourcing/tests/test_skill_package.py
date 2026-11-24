from __future__ import annotations

import os
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


DEFAULT_SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL_DIR = Path(os.environ.get("GAME_ART_SKILL_DIR", DEFAULT_SKILL_DIR))
EVALS_PATH = SKILL_DIR / "evals/evals.json"


class GameArtSourcingPackageTests(unittest.TestCase):
    @staticmethod
    def _read(relative_path: str) -> str:
        return (SKILL_DIR / relative_path).read_text(encoding="utf-8")

    @staticmethod
    def _normalized(text: str) -> str:
        return " ".join(text.split())

    def _section(self, text: str, start: str, end: str | None = None) -> str:
        self.assertIn(start, text)
        section = text.split(start, 1)[1]
        if end is not None:
            self.assertIn(end, section)
            section = section.split(end, 1)[0]
        return section

    def test_required_runtime_files_exist(self) -> None:
        required = [
            "SKILL.md",
            "README.md",
            "agents/openai.yaml",
            "block_builder.py",
            "cache_manager.py",
            "config.example.yaml",
            "feishu_client.py",
            "requirements.txt",
            "scripts/image_sourcing.py",
            "scripts/publish_to_feishu.py",
            "subskills/video-frame-art-analysis/SKILL.md",
            "references/art-technique-framework.md",
            "references/report-spec.md",
            "references/visual-system-stack.md",
            "references/history/README.md",
            "references/history/legacy/converter.py",
            "references/history/legacy/feishu_client.py",
            "references/history/legacy/publish_to_feishu.py",
            "references/history/source-git-log.md",
        ]
        missing = [path for path in required if not (SKILL_DIR / path).is_file()]
        self.assertEqual([], missing, f"missing packaged files: {missing}")

    def test_skill_is_codex_portable_and_respects_runtime_permissions(self) -> None:
        skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: game-art-sourcing", skill_text)
        self.assertRegex(skill_text, r"description:\s*Use when")
        self.assertIn("GAME_ART_SKILL_ROOT", skill_text)
        self.assertNotIn("~/.claude/skills/game-art-sourcing", skill_text)
        self.assertNotIn("绝对禁止请示", skill_text)
        self.assertNotIn("默认授权", skill_text)
        self.assertNotIn("已预先授予所有系统操作与网络请求权限", skill_text)
        self.assertNotIn("run_in_background", skill_text)
        self.assertNotIn("Agent tool", skill_text)
        self.assertNotIn("general-purpose", skill_text)
        self.assertNotIn("WebSearch", skill_text)
        self.assertLessEqual(
            len(skill_text.splitlines()),
            500,
            "SKILL.md should keep heavy reference material in references/",
        )

    def test_skill_automatically_composes_output_without_mode_selection(self) -> None:
        skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("不要让用户选择模式", skill_text)
        self.assertIn("核心输出始终保留", skill_text)
        self.assertIn("根据请求自动追加", skill_text)
        self.assertIn("多个模块", skill_text)

    def test_skill_documents_high_yield_image_route_and_negative_triggers(self) -> None:
        skill_text = self._read("SKILL.md")
        readme = self._read("README.md")
        video_skill = self._read("subskills/video-frame-art-analysis/SKILL.md")
        for term in [
            "30 秒美术定调卡",
            "Negative Triggers",
            "scripts/image_sourcing.py",
            "key_art",
            "character",
            "environment",
            "ui_gameplay",
            "少于 8 张",
            "quota_balance_requires_review",
            "风格基底词 + [主体替换位] + Negative Prompt",
        ]:
            with self.subTest(term=term):
                self.assertIn(term, skill_text)
        self.assertIn("/opt/homebrew/bin/yt-dlp", video_skill)
        self.assertIn("/usr/local/bin/ffmpeg", video_skill)
        self.assertIn("test_image_pipeline.py", readme)
        self.assertIn("--platform mobile", readme)

    def test_report_spec_uses_core_output_and_optional_modules(self) -> None:
        report_spec = (SKILL_DIR / "references/report-spec.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("核心输出", report_spec)
        self.assertIn("按需输出模块", report_spec)
        self.assertIn("模块选择规则", report_spec)
        self.assertIn("模块完成门禁", report_spec)
        self.assertNotIn("## 章节顺序", report_spec)

    def test_core_output_defines_three_required_results(self) -> None:
        documents = {
            "SKILL.md": (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8"),
            "report-spec.md": (
                SKILL_DIR / "references/report-spec.md"
            ).read_text(encoding="utf-8"),
        }
        for name, text in documents.items():
            normalized = " ".join(text.split())
            with self.subTest(document=name):
                self.assertIn("视觉一句话", normalized)
                self.assertRegex(normalized, r"1\s*[-–—~至]\s*3\s*条关键证据")
                self.assertRegex(
                    normalized,
                    r"(?:最关键|最值得).{0,12}(?:可复用|复用).{0,8}"
                    r"(?:警告|警惕).{0,8}结论",
                )

    def test_visual_system_reference_defines_all_layers_and_cross_checks(self) -> None:
        reference_path = SKILL_DIR / "references/visual-system-stack.md"
        self.assertTrue(reference_path.is_file(), f"missing {reference_path.name}")
        visual_system = reference_path.read_text(encoding="utf-8")
        required_terms = [
            "视觉身份",
            "资产系统",
            "体验系统",
            "品牌系统",
            "生产系统",
            "玩家情绪",
            "功能可读性",
            "进程或状态变化",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, visual_system)

    def test_skill_and_report_reference_visual_system_only_when_needed(self) -> None:
        documents = {
            "SKILL.md": (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8"),
            "report-spec.md": (
                SKILL_DIR / "references/report-spec.md"
            ).read_text(encoding="utf-8"),
        }
        for name, text in documents.items():
            normalized = " ".join(text.split())
            with self.subTest(document=name):
                self.assertIn("references/visual-system-stack.md", normalized)
                self.assertRegex(
                    normalized,
                    r"简单素材请求.{0,80}(?:不强制|无需|不要求|不得)"
                    r".{0,30}(?:完整读取|读取完整)",
                )

    def test_report_spec_requires_full_evidence_coverage_matrix(self) -> None:
        report_spec = (SKILL_DIR / "references/report-spec.md").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(report_spec.split())
        self.assertIn(
            "来源类型 × 游戏状态 × 时间/天气 × 镜头距离 × 进度阶段",
            report_spec,
        )
        self.assertRegex(
            normalized,
            r"简单.{0,20}(?:素材|请求).{0,80}(?:不强制|无需|不要求)"
            r".{0,30}(?:填满|覆盖).{0,20}矩阵",
        )
        self.assertRegex(
            normalized,
            r"完整分析.{0,80}(?:暴露|标明|明确).{0,30}(?:gap|缺口)",
        )

    def test_production_decision_card_ends_with_minimum_validation_action(self) -> None:
        report_spec = (SKILL_DIR / "references/report-spec.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("生产决策卡", report_spec)
        required_fields = [
            "观察",
            "证据与覆盖",
            "置信度",
            "可转移规则",
            "项目适配",
            "成本/风险",
            "最小验证动作",
        ]
        for field in required_fields:
            with self.subTest(field=field):
                self.assertIn(field, report_spec)

    def test_video_workflow_uses_adaptive_modules_and_project_decision_cards(
        self,
    ) -> None:
        video_skill = (
            SKILL_DIR / "subskills/video-frame-art-analysis/SKILL.md"
        ).read_text(encoding="utf-8")
        normalized = " ".join(video_skill.split())
        self.assertIn("按需追加", normalized)
        self.assertRegex(
            normalized,
            r"项目借鉴.{0,80}(?:生成|输出).{0,30}(?:生产)?决策卡",
        )
        self.assertRegex(
            normalized,
            r"(?:时间戳.{0,40}证据|证据.{0,40}时间戳)",
        )

    def test_video_annotations_classify_segments_and_share_evidence_contract(
        self,
    ) -> None:
        report_spec = self._read("references/report-spec.md")
        video_skill = self._read("subskills/video-frame-art-analysis/SKILL.md")
        annotation_schema = self._section(
            video_skill,
            "For each selected frame, record:",
            "Every technical claim needs visual evidence.",
        )

        segment_match = re.search(
            r'"segment_type"\s*:\s*"([^"]+)"',
            annotation_schema,
            flags=re.IGNORECASE,
        )
        self.assertIsNotNone(
            segment_match,
            "frame annotation schema must define segment_type",
        )
        step_6 = self._normalized(
            self._section(video_skill, "### Step 6:", "### Step 7:")
        ).lower()
        required_segment_categories = {
            "gameplay": r"gameplay|玩法",
            "cutscene": r"cutscene|过场",
            "pure promotional": r"pure[_ -]?promo(?:tional)?|纯宣传",
            "mixed": r"mixed|混合",
            "unknown": r"unknown|未确认",
        }
        for category, pattern in required_segment_categories.items():
            with self.subTest(segment_category=category):
                self.assertRegex(step_6, pattern)

        axis_keys = [
            "source_type",
            "game_state",
            "time_weather",
            "shot_distance",
            "progress_stage",
        ]
        for axis_key in axis_keys:
            with self.subTest(axis_key=axis_key):
                self.assertIn(f'"{axis_key}"', annotation_schema)

        canonical_terms = [
            "真实 gameplay 录屏",
            "官方 gameplay 演示",
            "过场/叙事演出",
            "建造/经营",
            "UI/菜单",
            "加载/转场",
            "室内受控光",
            "UI 全屏/不适用",
            "新手/开局",
            "终局/通关后",
            "赛季/活动",
        ]
        for term in canonical_terms:
            with self.subTest(parent_enum=term):
                self.assertIn(term, report_spec)

        normalized_video = self._normalized(video_skill)
        declares_single_source = bool(
            re.search(
                r"(?:五轴|轴值|枚举).{0,100}"
                r"(?:唯一真源|唯一来源|唯一规范|single source of truth)"
                r".{0,100}report-spec\.md",
                normalized_video,
                flags=re.IGNORECASE,
            )
            or re.search(
                r"report-spec\.md.{0,100}"
                r"(?:唯一真源|唯一来源|唯一规范|single source of truth)",
                normalized_video,
                flags=re.IGNORECASE,
            )
            or re.search(
                r"(?:五个字段|五轴).{0,80}必须直接使用.{0,80}"
                r"report-spec\.md.{0,80}(?:父五轴)?枚举.{0,80}"
                r"(?:不自行|不得自行|不能自行).{0,30}"
                r"(?:缩写|另造|改写)",
                normalized_video,
                flags=re.IGNORECASE,
            )
        )
        if declares_single_source:
            duplicate_axis_fields = [
                "source_type",
                "game_state",
                "shot_distance",
                "progress_stage",
            ]
            for axis_key in duplicate_axis_fields:
                value_match = re.search(
                    rf'"{axis_key}"\s*:\s*"([^"]+)"',
                    annotation_schema,
                )
                self.assertIsNotNone(value_match)
                self.assertNotIn(
                    " / ",
                    value_match.group(1),
                    f"{axis_key} must use one example value, not duplicate enums "
                    "that can drift from report-spec.md",
                )
        else:
            for term in canonical_terms:
                with self.subTest(child_enum=term):
                    self.assertIn(
                        term,
                        video_skill,
                        "child enums must match the parent unless report-spec.md "
                        "is declared as their single source of truth",
                    )

        for name, text in {
            "report-spec": report_spec,
            "video child": video_skill,
        }.items():
            with self.subTest(unknown_rule=name):
                self.assertRegex(
                    self._normalized(text),
                    r"(?:无法|不能).{0,30}确认.{0,30}(?:轴|取值).{0,20}"
                    r"(?:写|使用|标记|记录).{0,10}`?未确认`?",
                )
                self.assertRegex(
                    self._normalized(text),
                    r"(?:不能|不得|不要).{0,60}"
                    r"(?:猜测|推断|判断).{0,30}进度",
                )

    def test_video_synthesis_only_uses_requested_dimensions_unless_full_analysis(
        self,
    ) -> None:
        video_skill = self._read("subskills/video-frame-art-analysis/SKILL.md")
        normalized_video = self._normalized(video_skill)
        step_7 = self._normalized(
            self._section(video_skill, "### Step 7:", "### Step 8:")
        )
        step_8 = self._normalized(
            self._section(video_skill, "### Step 8:", "### Step 9:")
        )

        self.assertNotIn("Aggregate frame annotations into:", video_skill)
        self.assertRegex(
            step_7,
            r"(?:"
            r"(?:只|仅).{0,20}(?:综合|汇总|提炼).{0,30}"
            r"(?:用户|请求).{0,20}(?:点名|明确要求).{0,20}"
            r"(?:维度|内容|模块)"
            r"|only synthesize.{0,30}dimensions.{0,30}"
            r"(?:explicitly named|directly required).{0,30}request"
            r")",
        )
        self.assertRegex(
            f"{step_7} {step_8}",
            r"(?:"
            r"完整分析.{0,100}(?:所有|全部|完整).{0,30}"
            r"(?:有证据|证据支持|有画面证据).{0,30}(?:层|维度|模块)"
            r"|complete-analysis request.{0,40}synthesizes every relevant"
            r".{0,40}(?:dimension|layer).{0,30}(?:has evidence|evidence)"
            r")",
        )

    def test_video_preflight_and_fallback_both_stop_formal_reports_on_failure(
        self,
    ) -> None:
        video_skill = self._read("subskills/video-frame-art-analysis/SKILL.md")
        normalized_video = self._normalized(video_skill)
        preflight = self._normalized(
            self._section(video_skill, "### Step 3:", "### Step 4:")
        )
        fallbacks = self._normalized(self._section(video_skill, "## Fallbacks"))

        self.assertRegex(
            preflight.lower(),
            r"(?:if either tool is missing|缺少|缺失)",
        )
        self.assertRegex(
            preflight.lower(),
            r"(?:stop before formal report generation|停止.{0,30}正式报告)",
        )
        self.assertRegex(
            fallbacks,
            r"(?:frame extraction|抽帧).{0,30}(?:fails|failure|失败)"
            r".{0,100}(?:stop|停止).{0,60}(?:formal report|正式报告)",
        )
        self.assertRegex(
            normalized_video,
            r"(?:"
            r"(?:only|只有|仅).{0,30}(?:explicit|明确).{0,30}"
            r"(?:thumbnail-only smoke test|thumbnail only smoke test|缩略图"
            r"(?:冒烟|smoke test))"
            r"|(?:continue|继续).{0,20}(?:only|只有|仅).{0,30}"
            r"(?:explicit|明确).{0,30}"
            r"(?:thumbnail-only smoke test|thumbnail only smoke test|缩略图"
            r"(?:冒烟|smoke test))"
            r")",
        )
        self.assertRegex(
            normalized_video,
            r"(?:thumbnail-only smoke test|thumbnail only smoke test|缩略图"
            r"(?:冒烟|smoke test)).{0,220}(?:证据有限|limited evidence)",
        )

    def test_decision_card_contract_is_ordered_atomic_and_testable(self) -> None:
        report_spec = self._read("references/report-spec.md")
        video_skill = self._read("subskills/video-frame-art-analysis/SKILL.md")
        decision_section = self._normalized(
            self._section(report_spec, "## 生产决策卡", "## 模块完成门禁")
        )
        fixed_chain = (
            "观察 → 证据与覆盖 → 置信度 → 可转移规则 → 项目适配 "
            "→ 成本/风险 → 最小验证动作"
        )

        for name, text in {
            "report-spec": report_spec,
            "video child": video_skill,
        }.items():
            with self.subTest(document=name):
                self.assertIn(fixed_chain, self._normalized(text))

        self.assertRegex(
            decision_section,
            r"每条项目型重要建议.{0,40}(?:独立|单独).{0,20}"
            r"(?:生成|输出|写成).{0,20}(?:一张)?生产决策卡",
        )
        for required_part in [
            "测试对象",
            "目标镜头/状态",
            "对照变量",
            "通过/失败信号",
        ]:
            with self.subTest(validation_part=required_part):
                self.assertIn(required_part, decision_section)

    def test_report_spec_blocks_marketing_sources_from_proving_gameplay(self) -> None:
        report_spec = self._read("references/report-spec.md")
        bias_gate = self._normalized(
            self._section(report_spec, "### 展示偏差门禁", "## 生产决策卡")
        )

        for source_type in ["trailer", "Key Art", "商店截图"]:
            with self.subTest(source_type=source_type):
                self.assertIn(source_type, bias_gate)
        self.assertRegex(
            bias_gate,
            r"Key Art.{0,100}(?:不能|不得).{0,50}(?:gameplay|实时)",
        )
        self.assertRegex(
            bias_gate,
            r"商店截图.{0,100}(?:不能|不得).{0,50}(?:动态|失败状态|进度)",
        )
        self.assertRegex(
            bias_gate,
            r"trailer.{0,80}(?:区分|标明).{0,40}"
            r"(?:gameplay).{0,30}(?:过场).{0,30}(?:纯宣传)",
        )

    def test_video_keeps_real_frames_and_requires_explicit_prompt_or_feishu(
        self,
    ) -> None:
        report_spec = self._read("references/report-spec.md")
        video_skill = self._read("subskills/video-frame-art-analysis/SKILL.md")
        normalized_video = self._normalized(video_skill)
        lower_video = normalized_video.lower()

        self.assertRegex(
            lower_video,
            r"prefer real frames over thumbnails|优先.{0,20}真实",
        )
        self.assertRegex(
            lower_video,
            r"keep frame filenames timestamped|保留.{0,30}时间戳",
        )
        self.assertRegex(
            lower_video,
            r"(?:"
            r"(?:ai prompt|prompt).{0,80}(?:只有|仅|only).{0,30}"
            r"(?:用户|user).{0,20}(?:明确|explicit)"
            r"|only append ai prompt.{0,40}(?:user|用户).{0,20}"
            r"(?:explicitly|明确)"
            r")",
        )
        self.assertRegex(
            lower_video,
            r"(?:"
            r"(?:feishu|飞书).{0,80}(?:只有|仅|only).{0,30}"
            r"(?:用户|user).{0,20}(?:明确|explicit)"
            r"|only publish to feishu.{0,40}(?:user|用户).{0,20}"
            r"(?:explicitly|明确)"
            r")",
        )
        self.assertRegex(
            normalized_video,
            r"(?:不要沿用|移除|不再使用).{0,30}(?:固定七章|固定七章节)",
        )
        self.assertNotIn("## 章节顺序", report_spec)
        self.assertNotIn("固定七章报告结构", report_spec)

    def test_evals_cover_adaptive_output_and_negative_trigger_scenarios(self) -> None:
        eval_data = json.loads(EVALS_PATH.read_text(encoding="utf-8"))
        self.assertEqual("game-art-sourcing", eval_data["skill_name"])
        evals = eval_data["evals"]
        self.assertEqual(list(range(1, 9)), [item["id"] for item in evals])

        for item in evals:
            with self.subTest(eval_id=item["id"]):
                self.assertEqual(
                    {"id", "prompt", "expected_output", "files"},
                    set(item),
                )
                self.assertIsInstance(item["id"], int)
                self.assertIsInstance(item["prompt"], str)
                self.assertTrue(item["prompt"].strip())
                self.assertIsInstance(item["expected_output"], str)
                self.assertTrue(item["expected_output"].strip())
                self.assertIsInstance(item["files"], list)

        by_id = {item["id"]: item for item in evals}
        scenario_markers = {
            1: ("只帮我找", "不强制填满五维证据覆盖矩阵"),
            2: ("完整分析", "Visual System"),
            3: ("当前项目", "读取 Visual System"),
            4: ("YouTube", "video-first"),
            5: ("UI", "不触发 game-art-sourcing"),
            6: ("玩法循环", "不触发 game-art-sourcing"),
            7: ("买量广告", "不触发 game-art-sourcing"),
            8: ("收入", "不触发 game-art-sourcing"),
        }
        for eval_id, (prompt_marker, output_marker) in scenario_markers.items():
            with self.subTest(eval_id=eval_id):
                self.assertIn(prompt_marker, by_id[eval_id]["prompt"])
                self.assertIn(output_marker, by_id[eval_id]["expected_output"])

    def test_public_package_excludes_private_or_runtime_state(self) -> None:
        forbidden_names = {
            ".git",
            ".omc",
            ".claude",
            "config.yaml",
            "cache",
            "__pycache__",
        }
        violations = []
        for path in SKILL_DIR.rglob("*"):
            relative = path.relative_to(SKILL_DIR)
            if any(part in forbidden_names for part in relative.parts):
                violations.append(str(relative))
            if path.suffix in {".key", ".pem", ".token", ".pub"}:
                violations.append(str(relative))
        self.assertEqual([], sorted(set(violations)))

    def test_python_sources_compile(self) -> None:
        sources = [
            "block_builder.py",
            "cache_manager.py",
            "feishu_client.py",
            "scripts/image_sourcing.py",
            "scripts/publish_to_feishu.py",
        ]
        for source in sources:
            source_path = SKILL_DIR / source
            compile(
                source_path.read_text(encoding="utf-8"),
                str(source_path),
                "exec",
            )

    def test_runtime_state_and_credentials_live_outside_installed_skill(self) -> None:
        cache_text = (SKILL_DIR / "cache_manager.py").read_text(encoding="utf-8")
        publish_text = (SKILL_DIR / "scripts/publish_to_feishu.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("GAME_ART_CACHE_DIR", cache_text)
        self.assertIn("XDG_CACHE_HOME", cache_text)
        self.assertIn("--config", publish_text)
        self.assertIn("GAME_ART_CONFIG", publish_text)
        self.assertIn("XDG_CONFIG_HOME", publish_text)
        self.assertNotIn(
            'CONFIG_PATH = os.path.join(PROJECT_DIR, "config.yaml")',
            publish_text,
        )
        self.assertNotIn("load_html", publish_text)

    def test_document_permission_grant_requires_explicit_opt_in(self) -> None:
        module_path = SKILL_DIR / "feishu_client.py"
        spec = importlib.util.spec_from_file_location("game_art_feishu_client", module_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        response = MagicMock()
        response.json.return_value = {
            "code": 0,
            "data": {"document": {"document_id": "doc-test"}},
        }
        response.raise_for_status.return_value = None

        with patch.object(module.requests, "post", return_value=response):
            client = module.FeishuClient(
                app_id="test",
                app_secret="test",
                user_open_id="ou_test",
                grant_user_access=False,
            )
            client._ensure_token = MagicMock()
            client._token = "test-token"
            client._grant_permission = MagicMock()
            client.create_document("test")
            client._grant_permission.assert_not_called()

            opted_in = module.FeishuClient(
                app_id="test",
                app_secret="test",
                user_open_id="ou_test",
                grant_user_access=True,
            )
            opted_in._ensure_token = MagicMock()
            opted_in._token = "test-token"
            opted_in._grant_permission = MagicMock()
            opted_in.create_document("test")
            opted_in._grant_permission.assert_called_once_with("doc-test", "ou_test")

    def test_validate_only_converts_without_credentials_or_network(self) -> None:
        report = {
            "title": "Local validation",
            "blocks": [
                {
                    "type": "text",
                    "elements": [{"text": "evidence-backed art note"}],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "report.json"
            report_path.write_text(
                json.dumps(report, ensure_ascii=False),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(SKILL_DIR / "scripts/publish_to_feishu.py"),
                    str(report_path),
                    "--validate-only",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Validated: 1 blocks, 0 images", result.stdout)


if __name__ == "__main__":
    unittest.main()
