"""One circuit-breaker policy for collection consumers, including legacy inputs."""

import argparse
import json
import math
from pathlib import Path


def health_counts(scan: dict):
    health = scan.get("source_health") or {}
    summary = scan.get("source_summary") or {}
    return (int(health.get("succeeded_channels", summary.get("channels_succeeded", 0))),
            int(health.get("active_channels", summary.get("channels_attempted", 0))))


def health_banner(scan: dict) -> str:
    ok, total = health_counts(scan)
    return f"【数据降级：仅覆盖 {ok}/{total} 频道】"


def is_degraded(scan: dict) -> bool:
    health = scan.get("source_health") or {}
    summary = scan.get("source_summary") or {}
    if scan.get("run_status") not in (None, "RUN_OK"):
        return True
    if (summary.get("status") == "blocked" or health.get("probe_status") in {"failed", "error"}
            or health.get("degraded_reason")):
        return True
    if not health and "channels_attempted" not in summary:
        # Old inputs can describe a successful fallback only as "degraded".
        # This is not the same as a tripped circuit breaker.
        return scan.get("run_status") is not None
    threshold = float(health.get("healthy_threshold", 0.60))
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("invalid healthy_threshold")
    ok, total = health_counts(scan)
    if total <= 0 or not 0 <= ok <= total or ok / total < threshold:
        return True
    rate = float(health.get("healthy_rate", ok / total))
    return not (threshold <= rate <= 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", type=Path, required=True)
    try:
        print("RUN_DEGRADED" if is_degraded(json.loads(parser.parse_args().status.read_text())) else "RUN_OK")
    except (ValueError, TypeError, AttributeError, OSError) as exc:
        parser.exit(1, f"invalid scan input: {exc}\n")
