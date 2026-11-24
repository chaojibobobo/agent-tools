from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SKILL_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


image_sourcing = _load_module(
    "game_art_image_sourcing",
    SCRIPTS_DIR / "image_sourcing.py",
)
publish = _load_module(
    "game_art_publish_pipeline",
    SCRIPTS_DIR / "publish_to_feishu.py",
)
feishu = _load_module(
    "game_art_feishu_pipeline",
    SKILL_DIR / "feishu_client.py",
)
block_builder = _load_module(
    "game_art_block_builder_pipeline",
    SKILL_DIR / "block_builder.py",
)


class SourceParsingTests(unittest.TestCase):
    def test_steam_search_and_appdetails_extract_full_images(self) -> None:
        app_id = image_sourcing.parse_steam_search(
            {"items": [{"id": 1620290, "name": "Zombie Cure Lab"}]},
            "Zombie Cure Lab",
        )
        self.assertEqual(1620290, app_id)

        payload = {
            "1620290": {
                "success": True,
                "data": {
                    "header_image": "https://cdn.example/header.jpg",
                    "screenshots": [
                        {"id": 0, "path_full": "https://cdn.example/1.jpg"},
                        {"id": 1, "path_full": "https://cdn.example/2.jpg"},
                    ],
                },
            }
        }
        candidates = image_sourcing.parse_steam_appdetails(1620290, payload)
        self.assertEqual(3, len(candidates))
        self.assertEqual("key_art", candidates[0].bucket)
        self.assertEqual("https://cdn.example/1.jpg", candidates[1].url)
        self.assertTrue(all(item.tier == "L1" for item in candidates))

    def test_itunes_extracts_phone_ipad_and_key_art(self) -> None:
        payload = {
            "results": [
                {
                    "trackName": "Last War:Survival",
                    "artworkUrl512": "https://cdn.example/icon.jpg",
                    "screenshotUrls": ["https://cdn.example/p1.jpg"],
                    "ipadScreenshotUrls": ["https://cdn.example/t1.jpg"],
                }
            ]
        }
        candidates = image_sourcing.parse_itunes_search(payload, "Last War")
        self.assertEqual(3, len(candidates))
        self.assertEqual("key_art", candidates[0].bucket)
        self.assertEqual(
            {"https://cdn.example/p1.jpg", "https://cdn.example/t1.jpg"},
            {item.url for item in candidates[1:]},
        )

    def test_google_play_html_extracts_full_gallery_not_only_og_image(self) -> None:
        page = """
        <html><script>
        ["https://play-lh.googleusercontent.com/a=w1052-h592-rw",
         "https://play-lh.googleusercontent.com/b=w1052-h592-rw",
         "https://play-lh.googleusercontent.com/c=w1052-h592-rw"]
        </script></html>
        """
        candidates = image_sourcing.parse_google_play_html(page)
        self.assertEqual(3, len(candidates))
        self.assertEqual("key_art", candidates[0].bucket)
        self.assertTrue(all(item.url.endswith("=s0") for item in candidates))
        self.assertTrue(all(item.source_label == "Google Play" for item in candidates))

    def test_artstation_project_json_extracts_all_assets(self) -> None:
        payload = {
            "cover_url": "https://cdna.artstation.com/cover.jpg",
            "assets": [
                {"image_url": "https://cdna.artstation.com/character.jpg", "title": "Character turnaround"},
                {"image_url": "https://cdna.artstation.com/hud.jpg", "title": "HUD exploration"},
                {"image_url": "https://cdna.artstation.com/scene.jpg", "title": "Environment"},
            ],
        }
        candidates = image_sourcing.parse_artstation_project(payload)
        self.assertEqual(4, len(candidates))
        self.assertEqual("character", candidates[1].bucket)
        self.assertEqual("ui_gameplay", candidates[2].bucket)
        self.assertTrue(all(item.referer for item in candidates))

    def test_l1_shortfall_routes_to_l2_then_l3(self) -> None:
        calls = []

        def provider(name, candidates):
            def run():
                calls.append(name)
                return candidates

            return run

        c = image_sourcing.ImageCandidate
        result = image_sourcing.run_tiered_collection(
            quotas={"key_art": 1, "character": 2, "environment": 2, "ui_gameplay": 2},
            l1_provider=provider(
                "L1",
                [c("k", "key_art", "L1", "Steam")],
            ),
            l2_provider=provider(
                "L2",
                [
                    c("c", "character", "L2", "ArtStation"),
                    c("e", "environment", "L2", "Press Kit"),
                ],
            ),
            l3_provider=provider(
                "L3",
                [
                    c("u1", "ui_gameplay", "L3", "video"),
                    c("u2", "ui_gameplay", "L3", "video"),
                    c("c2", "character", "L3", "video"),
                    c("e2", "environment", "L3", "video"),
                ],
            ),
            min_total=6,
        )
        self.assertEqual(["L1", "L2", "L3"], calls)
        self.assertTrue(result.l3_triggered)
        self.assertEqual(7, len(result.selected))
        self.assertEqual(2, result.counts["ui_gameplay"])

    def test_l1_quota_hit_does_not_call_fallbacks(self) -> None:
        c = image_sourcing.ImageCandidate
        fallback = MagicMock(return_value=[])
        result = image_sourcing.run_tiered_collection(
            quotas={"key_art": 1, "character": 1, "environment": 1, "ui_gameplay": 1},
            l1_provider=lambda: [
                c("k", "key_art", "L1", "Steam"),
                c("c", "character", "L1", "Steam"),
                c("e", "environment", "L1", "Steam"),
                c("u", "ui_gameplay", "L1", "Steam"),
            ],
            l2_provider=fallback,
            l3_provider=fallback,
            min_total=4,
        )
        self.assertEqual(4, len(result.selected))
        fallback.assert_not_called()


class ImageQualityTests(unittest.TestCase):
    @staticmethod
    def _gradient(width: int, height: int, image_format: str = "PNG") -> bytes:
        image = Image.new("RGB", (width, height))
        for y in range(height):
            for x in range(width):
                image.putpixel((x, y), ((x * 3) % 256, (y * 5) % 256, (x + y) % 256))
        output = io.BytesIO()
        image.save(output, format=image_format)
        return output.getvalue()

    def test_webp_is_standardized_for_feishu(self) -> None:
        raw = self._gradient(640, 360, "WEBP")
        processed = image_sourcing.normalize_for_feishu_bytes(raw)
        self.assertIn(processed.format, {"JPEG", "PNG"})
        reopened = Image.open(io.BytesIO(processed.data))
        self.assertIn(reopened.format, {"JPEG", "PNG"})
        self.assertGreaterEqual(processed.width, 480)

    def test_low_resolution_image_is_rejected(self) -> None:
        raw = self._gradient(320, 180)
        with self.assertRaisesRegex(image_sourcing.ImageRejected, "low_resolution"):
            image_sourcing.normalize_for_feishu_bytes(raw)

    def test_flat_color_and_extreme_banner_are_rejected(self) -> None:
        flat = Image.new("RGB", (640, 360), "#557799")
        flat_buffer = io.BytesIO()
        flat.save(flat_buffer, format="PNG")
        with self.assertRaisesRegex(image_sourcing.ImageRejected, "pure_color"):
            image_sourcing.normalize_for_feishu_bytes(flat_buffer.getvalue())
        banner = self._gradient(1600, 300)
        with self.assertRaisesRegex(image_sourcing.ImageRejected, "banner"):
            image_sourcing.normalize_for_feishu_bytes(banner)

    def test_byte_and_perceptual_duplicate_detection(self) -> None:
        raw = self._gradient(640, 360)
        source = Image.open(io.BytesIO(raw))
        larger_image = source.resize((1280, 720), Image.Resampling.LANCZOS)
        larger_buffer = io.BytesIO()
        larger_image.save(larger_buffer, format="PNG")
        larger = larger_buffer.getvalue()
        first = image_sourcing.normalize_for_feishu_bytes(raw)
        second = image_sourcing.normalize_for_feishu_bytes(larger)
        deduper = image_sourcing.ImageDeduplicator(max_hamming=6)
        self.assertFalse(deduper.add(first))
        self.assertTrue(deduper.add(first))
        self.assertTrue(deduper.add(second))

    def test_palette_is_real_hex_with_ratios_and_ignores_black_white(self) -> None:
        image = Image.new("RGB", (100, 100), "#ffffff")
        image.paste("#cc3311", (0, 0, 100, 60))
        image.paste("#2266aa", (0, 60, 100, 90))
        image.paste("#111111", (0, 90, 100, 100))
        palette = image_sourcing.extract_hex_palette(image, colors=3)
        self.assertGreaterEqual(len(palette), 2)
        self.assertEqual("#CC3311", palette[0]["hex"])
        self.assertGreater(palette[0]["ratio"], palette[1]["ratio"])
        self.assertTrue(all(item["hex"] not in {"#FFFFFF", "#111111"} for item in palette))


class PublishGateTests(unittest.TestCase):
    def test_block_builder_preserves_bucket_and_same_bucket_alternates(self) -> None:
        report = {
            "blocks": [{
                "type": "image",
                "url": "/tmp/primary.jpg",
                "bucket": "environment",
                "source_tier": "L1",
                "source_label": "Steam",
                "alternates": ["/tmp/fallback.jpg"],
                "caption": "scene evidence",
            }]
        }
        blocks, image_map, meta = block_builder.json_to_blocks_with_metadata(report)
        self.assertEqual("/tmp/primary.jpg", image_map[0])
        self.assertEqual("environment", meta[0]["bucket"])
        self.assertEqual("environment", meta[0]["alternates"][0]["bucket"])
        self.assertEqual(1, meta[0]["caption_index"])
        self.assertEqual(2, len(blocks))

    def test_upload_retries_once_then_uses_same_bucket_alternate(self) -> None:
        client = feishu.FeishuClient("app", "secret")
        client._append_single_block = MagicMock(return_value="img-block")
        client._append_blocks = MagicMock(return_value=[])
        client._verify_document_content = MagicMock()
        client._delete_block = MagicMock()
        client._fill_image = MagicMock(
            side_effect=[RuntimeError("primary-1"), RuntimeError("primary-2"), None]
        )
        blocks = [{"block_type": 27, "image": {}}]
        with patch.object(feishu.time, "sleep", return_value=None):
            receipt = client.create_blocks(
                "doc",
                blocks,
                image_map={0: b"primary"},
                image_alternates={0: [b"same-bucket-alternate"]},
            )
        self.assertEqual(1, receipt["written_images"])
        self.assertEqual(1, receipt["replacements"])
        self.assertEqual(3, client._fill_image.call_count)
        client._delete_block.assert_not_called()

    def test_failed_placeholder_uses_feishu_parent_range_delete_api(self) -> None:
        client = feishu.FeishuClient("app", "secret")
        client._ensure_token = MagicMock()
        client._token = "token"
        get_response = MagicMock()
        get_response.raise_for_status.return_value = None
        get_response.json.return_value = {
            "code": 0,
            "data": {
                "items": [
                    {"block_id": "text-1", "block_type": 2},
                    {"block_id": "failed-image", "block_type": 27},
                    {"block_id": "text-2", "block_type": 2},
                ],
                "has_more": False,
            },
        }
        delete_response = MagicMock()
        delete_response.raise_for_status.return_value = None
        delete_response.json.return_value = {"code": 0, "data": {}}
        with patch.object(feishu.requests, "get", return_value=get_response):
            with patch.object(
                feishu.requests, "delete", return_value=delete_response
            ) as delete:
                with patch.object(feishu.time, "sleep", return_value=None):
                    self.assertTrue(
                        client._delete_child_blocks("doc", ["failed-image"])
                    )
        self.assertTrue(
            delete.call_args.args[0].endswith(
                "/documents/doc/blocks/doc/children/batch_delete"
            )
        )
        self.assertEqual(
            {"start_index": 1, "end_index": 2},
            delete.call_args.kwargs["json"],
        )

    def test_incomplete_gate_builds_top_notice_and_three_line_receipt(self) -> None:
        image_meta = {
            0: {"bucket": "key_art", "source_label": "Steam"},
            1: {"bucket": "environment", "source_label": "video"},
            2: {"bucket": "ui_gameplay", "source_label": "video"},
        }
        receipt = publish.build_image_receipt(
            requested=8,
            successful_indices={0, 1, 2},
            image_meta=image_meta,
            failures=[{"reason": "low_resolution"}, {"reason": "upload_failed"}],
        )
        self.assertTrue(receipt["incomplete"])
        self.assertIn("character", receipt["missing_buckets"])
        notice = publish.build_incomplete_notice(receipt)
        self.assertTrue(notice.startswith("【图源残缺：总计 3 张，缺失板块："))
        lines = publish.format_publish_receipt("https://feishu.test/doc", receipt).splitlines()
        self.assertEqual(3, len(lines))
        self.assertEqual("https://feishu.test/doc", lines[0])
        self.assertIn("入图 3/8", lines[1])
        self.assertIn("剔除/失败：5 张", lines[2])


if __name__ == "__main__":
    unittest.main()
