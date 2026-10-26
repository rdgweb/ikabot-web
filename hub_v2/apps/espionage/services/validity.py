"""N-163: how long a spy report counts, by mission.

Invenções (26) 15 days, Guarnição (6) 5 days, Armazém (5) 4 days when the city is rich and 8 when it is poor;
any other mission, and a mission that failed, the general spy_report_expiry_hours (48 h). Counted from when the
report was first saved, so collecting it again does not make it last longer. Each value is an AppSetting.
"""
from __future__ import annotations

from datetime import timedelta

STOCK_KEYS = ("wood", "wine", "marble", "crystal", "glass", "sulfur")
DEFAULTS = {
    "spy_report_expiry_hours": 48,
    "spy_ttl_inventions_hours": 15 * 24,
    "spy_ttl_garrison_hours": 5 * 24,
    "spy_ttl_stock_rich_hours": 4 * 24,
    "spy_ttl_stock_poor_hours": 8 * 24,
    "spy_rich_threshold": 200_000,
}


def setting(key: str) -> int:
    from apps.settings_app.models import AppSetting

    try:
        return int(AppSetting.objects.get(key=key).value)
    except (AppSetting.DoesNotExist, TypeError, ValueError):
        return DEFAULTS[key]


def stock_total(data_json) -> int:
    data = data_json if isinstance(data_json, dict) else {}
    total = 0
    for key in STOCK_KEYS:
        try:
            total += int(data.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return total


def ttl_hours(mission_id, data_json, success: bool) -> int:
    if not success:
        return setting("spy_report_expiry_hours")
    mission = int(mission_id or 0)
    if mission == 26:
        return setting("spy_ttl_inventions_hours")
    if mission == 6:
        return setting("spy_ttl_garrison_hours")
    if mission == 5:
        rich = stock_total(data_json) >= setting("spy_rich_threshold")
        return setting("spy_ttl_stock_rich_hours" if rich else "spy_ttl_stock_poor_hours")
    return setting("spy_report_expiry_hours")


def expires_at_for(report):
    """When this report stops counting (from the moment it was first saved)."""
    return report.created_at + timedelta(hours=ttl_hours(report.mission_id, report.data_json, report.is_mission_success))
