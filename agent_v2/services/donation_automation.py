"""Shared automation helpers for donation runners."""

from __future__ import annotations

import time
from typing import Any


RETRY_BUFFER_SECONDS = 90


def compute_due_delay(base_delay: int, project_state: dict[str, Any]) -> int:
    """Delay the next cycle until the island can receive donations again."""
    if not project_state.get("is_upgrading"):
        return max(60, int(base_delay))
    finish_at_ts = int(project_state.get("finish_at_ts") or 0)
    if finish_at_ts <= 0:
        return max(60, int(base_delay))
    now_ts = int(time.time())
    if now_ts + int(base_delay) >= finish_at_ts:
        return max(60, int(base_delay))
    return max(60, finish_at_ts - now_ts + RETRY_BUFFER_SECONDS)


def build_modify_production_inputs(
    city_id: str,
    city_name: str,
    baseline: dict[str, Any],
    *,
    restore_mode: str = "preserve",
    custom_sawmill_percent: Any = None,
    custom_luxury_percent: Any = None,
) -> dict[str, Any]:
    mode = str(restore_mode or "preserve").strip().lower()
    if mode == "custom":
        try:
            sawmill_percent = max(0, min(150, int(custom_sawmill_percent)))
        except Exception:
            sawmill_percent = int(baseline["resource"]["percent"])
        try:
            luxury_percent = max(0, min(150, int(custom_luxury_percent)))
        except Exception:
            luxury_percent = int(baseline["tradegood"]["percent"])
    else:
        sawmill_percent = str(baseline["resource"]["percent"])
        luxury_percent = str(baseline["tradegood"]["percent"])
    return {
        "city_id": str(city_id),
        "city_name": city_name,
        "sawmill_percent": sawmill_percent,
        "luxury_percent": luxury_percent,
    }
