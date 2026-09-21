from core.context_processors import _is_admin

from . import services


def telemetry_prompt(request):
    """Expose whether the opt-in banner must be shown (admins only, while undecided)."""
    user = getattr(request, "user", None)
    try:
        if not _is_admin(user):
            return {}
        return {
            "TELEMETRY_PROMPT": services.needs_decision(),
            "TELEMETRY_PUBLIC_URL": services.endpoint(),
        }
    except Exception:  # noqa: BLE001 - never break page rendering
        return {}
