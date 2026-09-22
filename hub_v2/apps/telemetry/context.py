from core.context_processors import _is_admin

from . import services


def telemetry_prompt(request):
    """First-use notice: shown to admins until someone decides. Showing it starts the daily ping."""
    user = getattr(request, "user", None)
    try:
        if not _is_admin(user) or not services.needs_notice():
            return {}
        services.mark_notice_shown()
        return {"TELEMETRY_PROMPT": True, "TELEMETRY_PUBLIC_URL": services.endpoint()}
    except Exception:  # noqa: BLE001 - never break page rendering
        return {}
