"""Welcome tour (N-116): shown once to each user, switched on or off in Configurações.

The tour itself is static/js/tour.js; here only who still has to see it. Switching it on again
shows it once more to everybody.
"""
from __future__ import annotations

from django.utils import timezone

ENABLED_KEY = "tour_enabled"
DONE_PREFIX = "tour_done:"


def _done_key(user) -> str:
    return f"{DONE_PREFIX}{user.pk}"


def is_enabled() -> bool:
    from .models import AppSetting

    value = AppSetting.objects.filter(key=ENABLED_KEY).values_list("value", flat=True).first()
    return value != "0"                # on unless someone switched it off


def should_autostart(user) -> bool:
    from .models import AppSetting

    if not getattr(user, "is_authenticated", False) or not is_enabled():
        return False
    return not AppSetting.objects.filter(key=_done_key(user)).exists()


def mark_done(user) -> None:
    from .models import AppSetting

    AppSetting.objects.update_or_create(key=_done_key(user), defaults={"value": timezone.now().isoformat(timespec="seconds")})


def set_enabled(enabled: bool) -> None:
    from .models import AppSetting

    AppSetting.objects.update_or_create(key=ENABLED_KEY, defaults={"value": "1" if enabled else "0"})
    if enabled:
        AppSetting.objects.filter(key__startswith=DONE_PREFIX).delete()


def state() -> dict:
    """What the installation already has, so each step of the tour talks about the real hub."""
    from apps.accounts.models import Account, GameAccount, Node

    nodes = list(Node.objects.all())
    account = Account.objects.order_by("created_at").first()
    return {
        "nodes": len(nodes),
        "online": sum(1 for node in nodes if node.is_online),
        "accounts": Account.objects.count(),
        "worlds": GameAccount.objects.filter(active=True).count(),
        "first_account": str(account.pk) if account else "",
    }


def tour_context(request):
    """TOUR_AUTOSTART is only queried by the full page (base.html), not by every partial."""
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return {}
    return {"TOUR_AUTOSTART": lambda: should_autostart(user)}
