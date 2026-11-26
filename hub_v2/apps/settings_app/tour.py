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


def restart(user) -> None:
    """N-126: the person asked to see the tour again; it starts on the next page."""
    from .models import AppSetting

    AppSetting.objects.filter(key=_done_key(user)).delete()


def set_enabled(enabled: bool) -> None:
    from .models import AppSetting

    AppSetting.objects.update_or_create(key=ENABLED_KEY, defaults={"value": "1" if enabled else "0"})
    if enabled:
        AppSetting.objects.filter(key__startswith=DONE_PREFIX).delete()


def state(organization=None) -> dict:
    """What the installation already has, so each step of the tour talks about the real hub. N-203: a customer of a
    hub whose platform runs agents is led to the platform's agent (no Docker at home) instead of a node of its own."""
    from apps.accounts import platform_agent
    from apps.accounts.models import Account, GameAccount, Node
    from apps.orgs.services import DEFAULT_SLUG
    from apps.billing import plans

    nodes = list(Node.objects.all())
    account = Account.objects.order_by("created_at").first()
    customer = organization is not None and getattr(organization, "slug", DEFAULT_SLUG) != DEFAULT_SLUG
    return {
        "nodes": len(nodes),
        "online": sum(1 for node in nodes if node.is_online),
        "accounts": Account.objects.count(),
        "worlds": GameAccount.objects.filter(active=True).count(),
        "first_account": str(account.pk) if account else "",
        "platform": bool(customer and platform_agent.host() is not None),
        "platform_agents": Account.objects.filter(platform_agent=True).count(),
        "platform_agent_price": plans.brl(plans.ADDON_AGENT),
        "platform_proxy_price": plans.brl(plans.ADDON_PROXIES),
    }


def tour_context(request):
    """TOUR_AUTOSTART is only queried by the full page (base.html), not by every partial."""
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return {}
    return {"TOUR_AUTOSTART": lambda: should_autostart(user)}
