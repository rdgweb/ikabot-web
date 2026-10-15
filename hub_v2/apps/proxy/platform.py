"""Platform proxies add-on (N-129): 3 proxies of the platform's pool, lent to one customer's GameForge account.

While lent, a proxy belongs to the customer's organization (so the agent of that organization sees it)
and is reserved for that account only; it is never handed to another account or organization. When
the add-on ends, it goes back to the platform's pool. The platform keeps a reserve for its own accounts.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

PROXIES_PER_ACCOUNT = 3
PLATFORM_RESERVE = 15        # free proxies always kept for the platform's own accounts


def _platform_org():
    from apps.orgs.services import default_organization

    return default_organization()


def platform_free():
    """The platform's proxies anyone may take: healthy, not reserved, not a node's, not lent."""
    from .models import ProxyProfile

    return ProxyProfile._base_manager.filter(
        organization=_platform_org(), active=True, last_test_status=True,
        account_reservation__isnull=True, assigned_node__isnull=True, lent_to__isnull=True,
    ).order_by("last_test_latency_ms", "address")


def accounts_available() -> int:
    """How many more customer accounts the pool can take."""
    return max(0, platform_free().count() - PLATFORM_RESERVE) // PROXIES_PER_ACCOUNT


def lend(account, wanted: int) -> list:
    """Lend up to `wanted` platform proxies to this account (reserved for it). Returns them."""
    from .models import AccountProxyReservation, ProxyProfile

    if wanted <= 0:
        return []
    with transaction.atomic():
        free = list(ProxyProfile._base_manager.select_for_update().filter(
            pk__in=list(platform_free().values_list("pk", flat=True)[:wanted])))
        for proxy in free:
            proxy.organization_id = account.organization_id
            proxy.lent_to = account
            proxy.save(update_fields=["organization", "lent_to"])
            AccountProxyReservation._base_manager.create(account=account, proxy_profile=proxy)
    if free:
        _point_node(account)
    return free


def _point_node(account) -> None:
    """The node of an account without a proxy uses one of the account's own 3 (no fourth proxy)."""
    from .models import ProxyProfile

    node = account.node
    if node is None or node.proxy:
        return
    proxy = ProxyProfile._base_manager.filter(lent_to=account, active=True, last_test_status=True,
                                              assigned_node__isnull=True).first()
    if proxy is None:
        return
    proxy.assigned_node = node
    proxy.save(update_fields=["assigned_node"])
    node.proxy = proxy.proxy_url
    node.save(update_fields=["proxy"])


def give_back(proxy) -> None:
    """One lent proxy returns to the platform: no reservation, no node, the platform's again."""
    from .models import AccountProxyReservation

    AccountProxyReservation._base_manager.filter(proxy_profile=proxy).delete()
    node = proxy.assigned_node
    if node is not None:
        if node.proxy == proxy.proxy_url:
            node.proxy = ""
            node.save(update_fields=["proxy"])
        proxy.assigned_node = None
    proxy.organization = _platform_org()
    proxy.lent_to = None
    proxy.save(update_fields=["assigned_node", "organization", "lent_to"])


def enable(account) -> list:
    """Switch the add-on on (or keep it on): fill the account up to 3 proxies."""
    from .models import AccountProxyReservation

    if not account.platform_proxies:
        account.platform_proxies_billed = False      # newly on: the next monthly charge includes it, whatever happens
    account.platform_proxies, account.platform_proxies_ends_at = True, None
    account.save(update_fields=["platform_proxies", "platform_proxies_ends_at", "platform_proxies_billed", "updated_at"])
    have = AccountProxyReservation._base_manager.filter(account=account, proxy_profile__active=True).count()
    return lend(account, PROXIES_PER_ACCOUNT - have)


def end(account) -> int:
    """The add-on is over: every lent proxy goes back. Returns how many."""
    from .models import ProxyProfile

    count = 0
    with transaction.atomic():
        for proxy in ProxyProfile._base_manager.select_for_update().filter(lent_to=account):
            give_back(proxy)
            count += 1
        account.platform_proxies, account.platform_proxies_ends_at, account.platform_proxies_billed = False, None, False
        account.save(update_fields=["platform_proxies", "platform_proxies_ends_at", "platform_proxies_billed", "updated_at"])
    return count


def end_due(organization=None, now=None, *, include_held: bool = True) -> int:
    """Add-ons switched off whose paid period is over, and every add-on of an organization whose plan
    no longer allows work (canceled and over, or unpaid past the grace days): the proxies go back."""
    from apps.accounts.models import Account
    from apps.billing.services import state_for

    now = now or timezone.now()
    with_addon = Account._base_manager.filter(platform_proxies=True)
    if organization is not None:
        with_addon = with_addon.filter(organization=organization)
    ended = 0
    held = {}
    for account in with_addon.select_related("organization"):
        if account.organization_id not in held:
            held[account.organization_id] = include_held and not state_for(account.organization, now).allows_work
        over = account.platform_proxies_ends_at is not None and account.platform_proxies_ends_at <= now
        if over or held[account.organization_id]:
            end(account)
            ended += 1
    return ended


def schedule_all_off(organization, until) -> int:
    """The subscription was canceled: every add-on ends with the period already paid."""
    from django.db.models import Q

    from apps.accounts.models import Account

    return Account._base_manager.filter(organization=organization, platform_proxies=True).filter(
        Q(platform_proxies_ends_at__isnull=True) | Q(platform_proxies_ends_at__gt=until)
    ).update(platform_proxies_ends_at=until)


def accounts_with_addon(organization) -> int:
    """Add-ons the next monthly charge includes: the ones on, and the ones switched off before ever being charged."""
    from django.db.models import Q

    from apps.accounts.models import Account

    return Account._base_manager.filter(organization=organization, platform_proxies=True).filter(
        Q(platform_proxies_ends_at__isnull=True) | Q(platform_proxies_billed=False)).count()


def mark_billed(organization) -> int:
    """A monthly charge was paid: every add-on in it has now been charged at least once."""
    from apps.accounts.models import Account

    return Account._base_manager.filter(organization=organization, platform_proxies=True,
                                        platform_proxies_billed=False).update(platform_proxies_billed=True)
