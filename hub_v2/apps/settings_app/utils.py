from __future__ import annotations

from .models import AppSetting


def get_setting(key: str, default: str = "") -> str:
    try:
        return AppSetting.objects.get(key=key).value
    except AppSetting.DoesNotExist:
        return default


def get_int_setting(key: str, default: int) -> int:
    raw = str(get_setting(key, str(default)) or "").strip()
    try:
        return int(raw)
    except Exception:
        return int(default)
