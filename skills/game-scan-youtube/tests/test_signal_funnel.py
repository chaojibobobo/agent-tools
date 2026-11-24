"""Exercise the publication boundary with local, deterministic evidence."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import validate_run

DATE = "2026-11-24"
URL = "https://www.youtube.com/watch?v=XYab12CD3ef"
REPORT = """# 日报
> **Scan Profile:** 4X-SLG-STRICT-v1

## New Games
### 边城 — 城建加占地
- [实机](%s) — 频道
**4X Fit:** Core
**Card Evidence:** 部队占领城外资源点
**Card Mechanism:** 招募部队→派兵占地
**Card Form:** 城建+领土4X
**Card Signal:** 部队可驻守
"""


class PublicationAudit(unittest.TestCase):
    def check(self, report, scan=None):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name in ("channels", "seen_games"):
                (root / f"{name}.json").write_text("{}")
            (root / f"{DATE}.md").write_text(report)
            if scan is not None:
                (root / f"scan-input-{DATE}.json").write_text(json.dumps(scan))
            return validate_run.validate(DATE, root, False)

    def test_degraded_quiet_without_ledger_is_not_complete(self):
        with self.assertRaisesRegex(ValueError, "RUN_DEGRADED"):
            self.check("# 日报\n> Scan Profile: 4X-SLG-STRICT-v1\n## Quiet Day\n",
                       {"run_status": "RUN_DEGRADED"})

    def test_low_coverage_cannot_be_overridden_by_ok_label(self):
        with self.assertRaisesRegex(ValueError, "仅覆盖 2/5"):
            self.check(REPORT % URL, {"run_status": "RUN_OK", "source_health": {
                "active_channels": 5, "succeeded_channels": 2, "healthy_rate": 1}})

    def test_source_unverified_cannot_be_laundered_through_short_url(self):
        with self.assertRaisesRegex(ValueError, "unverified.*边城|边城.*unverified"):
            self.check(REPORT % "https://youtu.be/XYab12CD3ef?t=6", {
                "videos": [{"url": URL, "time_status": "unverified"}]})

    def test_watch_page_instead_of_store_is_required_by_validate_not_only_push(self):
        with self.assertRaisesRegex(ValueError, "YouTube.*边城|边城.*YouTube"):
            self.check(REPORT % "https://play.google.com/store/apps/details?id=x")

    def test_bad_copy_rejected_by_workspace_validation(self):
        for phrase in ("值得持续关注", "攻" * 46):
            with self.subTest(phrase=phrase), self.assertRaisesRegex(ValueError, "机制|mechanism"):
                self.check((REPORT % URL).replace("招募部队→派兵占地", phrase)
                           .replace("**4X Fit:**", "**Priority:** Focus\n**4X Fit:**"))

    def test_unverified_channel_cannot_get_earliest_discovery_credit(self):
        from intelligence_state import _candidate_channel_facts
        videos = [{"time_status": status, "published": "2026-11-24T00:00:00Z",
                   "time_precision": "exact", "channel": {"channel_id": channel}}
                  for channel, status in [("UCearly", "unverified"), ("UCknown", "verified")]]
        facts = _candidate_channel_facts({"videos": videos})
        self.assertFalse(facts["UCearly"]["lead"])
        self.assertTrue(facts["UCknown"]["lead"])

    def test_pending_cannot_be_promoted_by_spelling_change(self):
        from signal_funnel import prepare_report
        parsed = {"games": [{"name": "Last-King", "videos": [{"url": URL}]}]}
        ledger = {"candidates": [{"canonical_name": "Last King", "classification": "Pending"}]}
        with self.assertRaisesRegex(ValueError, "Pending"):
            prepare_report(parsed, {}, ledger)

    def test_orphan_degradation_banner_is_not_a_quiet_report(self):
        with self.assertRaisesRegex(ValueError, "RUN_DEGRADED"):
            self.check("【数据降级：仅覆盖 1/5 频道】\n> Scan Profile: 4X-SLG-STRICT-v1\n## Quiet Day\n")

    def test_raw_precision_follows_ledger_into_the_bounded_recheck_queue(self):
        from intelligence_state import update_candidate_history
        from signal_funnel import prepare_report
        from test_intelligence_commit import core_candidate, ledger
        review = ledger([core_candidate(video_id="XYab12CD3ef")])
        scan = {"videos": [{"url": URL, "time_status": "unverified"}]}
        prepare_report({"games": []}, scan, review)
        first = update_candidate_history({}, review, DATE)
        record = first["candidates"]["pkg:com.fixture.lastking"]
        self.assertEqual(["XYab12CD3ef"], record["pending_video_ids"])
        corrected = ledger([core_candidate(video_id="XYab12CD3ef")])
        second = update_candidate_history(first, corrected, "2026-11-25")
        self.assertEqual([], second["candidates"]["pkg:com.fixture.lastking"]["pending_video_ids"])

    def test_old_resolved_upload_cannot_be_counted_as_todays_first_release(self):
        from signal_funnel import prepare_report
        parsed = {"games": [{"name": "边城", "videos": [{"url": URL}]}]}
        scan = {"videos": [{"url": URL, "verification_update": True,
                            "time_status": "verified", "published": "2026-11-23T07:00:00Z"}]}
        with self.assertRaisesRegex(ValueError, "补核"):
            prepare_report(parsed, scan)

    def test_ledger_join_accepts_a_short_url_of_the_same_video(self):
        from intelligence_state import validate_report_candidates
        from test_intelligence_commit import core_candidate, ledger
        review = ledger([core_candidate(video_id="XYab12CD3ef")])
        validate_report_candidates([{"name": "Last-King", "videos": [
            {"url": "https://youtu.be/XYab12CD3ef?t=33"}]}], review)

    def test_card_cannot_drop_nested_video_precision(self):
        import decision_card
        with self.assertRaisesRegex(ValueError, "unverified"):
            decision_card.validate_decision_copy({"games": [
                {"name": "边城", "videos": [{"url": URL, "time_status": "unverified"},
                                         {"url": "https://youtu.be/Vbcd12EF3ab", "time_status": "verified"}]}]})
