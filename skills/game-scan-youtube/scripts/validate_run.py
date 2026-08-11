#!/usr/bin/env python3
"""Machine-verifiable completion gate for a game-scan-youtube run."""

import argparse
import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import intelligence_state


def load_push_module():
    path = SCRIPT_DIR / "push_feishu.py"
    spec = importlib.util.spec_from_file_location("game_scan_push_feishu", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def validate(
    date: str,
    work_dir: Path,
    require_push_receipt: bool,
    require_intelligence_ledger: bool = False,
) -> dict:
    errors = []
    report_path = work_dir / f"{date}.md"
    if not report_path.is_file() or report_path.stat().st_size == 0:
        errors.append(f"missing or empty report: {report_path}")
        parsed = None
    else:
        try:
            parsed = load_push_module().parse_report(report_path)
            load_push_module().validate_4x_profile(
                parsed,
                require_strict=date >= "2026-08-06",
            )
            empty_video_games = [
                game["name"] for game in parsed["games"] if not game.get("videos")
            ]
            if empty_video_games:
                raise ValueError(
                    "game sections without video evidence: "
                    + ", ".join(empty_video_games)
                )
        except Exception as exc:
            errors.append(f"report parse failed: {exc}")
            parsed = None

    ledger = None
    ledger_path = work_dir / f"candidate-ledger-{date}.json"
    if require_intelligence_ledger or ledger_path.exists():
        try:
            if not ledger_path.is_file():
                raise ValueError(f"missing candidate ledger: {ledger_path}")
            ledger = json.loads(ledger_path.read_text())
            intelligence_state.validate_candidate_ledger(ledger)
            if require_intelligence_ledger:
                intelligence_state.validate_product_radar_run(ledger)
            if parsed is None:
                raise ValueError("report is unavailable for candidate-ledger validation")
            intelligence_state.validate_report_candidates(parsed["games"], ledger)
        except Exception as exc:
            errors.append(f"candidate ledger failed: {exc}")

    scan_input = None
    if require_intelligence_ledger or ledger is not None:
        scan_input_path = work_dir / f"scan-input-{date}.json"
        try:
            scan_input = json.loads(scan_input_path.read_text())
            source_status = (scan_input.get("source_summary") or {}).get("status")
            if source_status not in {"complete", "degraded"}:
                raise ValueError(f"source status is {source_status!r}")
            window = scan_input.get("window") or {}
            if not window.get("start") or not window.get("end"):
                raise ValueError("scan input has no complete window")
        except Exception as exc:
            errors.append(f"scan input failed: {exc}")

    state_names = ["channels.json", "seen_games.json"]
    if require_intelligence_ledger or ledger is not None:
        state_names.extend(
            ["candidate_history.json", "product_radar.json", "scan_state.json"]
        )
    for state_name in state_names:
        state_path = work_dir / state_name
        try:
            data = json.loads(state_path.read_text())
            if not isinstance(data, dict):
                raise ValueError("top-level value is not an object")
        except Exception as exc:
            errors.append(f"invalid {state_name}: {exc}")

    receipt = None
    if require_push_receipt:
        receipt_path = work_dir / "receipts" / f"{date}.feishu.json"
        try:
            receipt = json.loads(receipt_path.read_text())
            if receipt.get("date") != date:
                raise ValueError(f"receipt date is {receipt.get('date')!r}")
            if receipt.get("status") != "sent":
                raise ValueError(f"receipt status is {receipt.get('status')!r}")
            if not receipt.get("message_id"):
                raise ValueError("receipt has no message_id")
        except Exception as exc:
            errors.append(f"invalid Feishu receipt: {exc}")

    if errors:
        raise ValueError("; ".join(errors))

    return {
        "date": date,
        "status": "complete",
        "report": str(report_path),
        "is_quiet": parsed["is_quiet"],
        "game_count": len(parsed["games"]),
        "source_status": (scan_input or {}).get("source_summary", {}).get("status"),
        "candidate_count": len(ledger.get("candidates", [])) if ledger else None,
        "feishu_message_id": receipt.get("message_id") if receipt else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--dir", required=True, help="Scan workspace")
    parser.add_argument("--require-push-receipt", action="store_true")
    parser.add_argument("--require-intelligence-ledger", action="store_true")
    args = parser.parse_args()

    try:
        result = validate(
            args.date,
            Path(args.dir).expanduser(),
            args.require_push_receipt,
            args.require_intelligence_ledger,
        )
    except Exception as exc:
        print(f"VALIDATION FAILED: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
