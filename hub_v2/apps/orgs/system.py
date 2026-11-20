"""The system's administrator over every organization (N-203).

Everything that used to be done straight in the database — give a tester more days, exempt an
organization, let it use the platform's proxies and agent without charge, pause it, delete a test
organization — is an action here, recorded in the audit log. The system is its owner's: nothing here
needs anyone else.
"""
from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone


def _billing(organization):
    from apps.billing.services import account_for

    return account_for(organization)


def overview(organization) -> dict:
    """One organization as the administrator sees it."""
    from apps.accounts.models import Account, GameAccount, Node
    from apps.billing.services import active_subaccounts, free_addons, state_for
    from apps.settings_app.tour import _done_key
    from apps.settings_app.models import AppSetting

    from .models import Membership

    billing = _billing(organization)
    state = state_for(organization)
    members = list(Membership._base_manager.filter(organization=organization).select_related("user"))
    accounts = list(Account._base_manager.filter(organization=organization).select_related("node").order_by("label"))
    nodes = list(Node._base_manager.filter(organization=organization))
    tour_done = set(AppSetting.objects.filter(key__in=[_done_key(m.user) for m in members]).values_list("key", flat=True))
    free, free_until, free_why = free_addons(billing)
    return {
        "organization": organization,
        "billing": billing,
        "state": state,
        "members": [{"user": m.user, "role": m.role, "tour_done": _done_key(m.user) in tour_done,
                     "last_login": m.user.last_login} for m in members],
        "accounts": [{"account": a, "worlds": GameAccount._base_manager.filter(account=a).count(),
                      "node_online": bool(a.node_id and a.node.is_online)} for a in accounts],
        "subaccounts": active_subaccounts(organization),
        "nodes": len(nodes), "nodes_online": sum(1 for n in nodes if n.is_online),
        "proxies_on": sum(1 for a in accounts if a.platform_proxies),
        "agents_on": sum(1 for a in accounts if a.platform_agent),
        "free_addons": free, "free_until": free_until, "free_why": free_why,
        "is_platform": organization.slug == _default_slug(),
    }


def _default_slug() -> str:
    from .services import DEFAULT_SLUG

    return DEFAULT_SLUG


def _record(action: str, organization, **details) -> None:
    from apps.audit.services import record

    record(action, target=organization, target_label=organization.name, details=details or None)


def extend_trial(organization, days: int) -> None:
    """More days of free trial, counted from today or from its current end, whichever is later."""
    from apps.billing.models import BillingAccount

    billing = _billing(organization)
    base = max(timezone.now(), billing.trial_ends_at or timezone.now())
    billing.trial_ends_at = base + timedelta(days=int(days))
    if billing.status not in (BillingAccount.ACTIVE, BillingAccount.EXEMPT):
        billing.status = BillingAccount.TRIAL
    billing.save(update_fields=["trial_ends_at", "status", "updated_at"])
    _record("organization.trial_extended", organization, days=int(days), until=billing.trial_ends_at.isoformat())


def set_exempt(organization, exempt: bool) -> None:
    """Exempt: works with no plan and no end. Taken back, it is a trial again (its trial end as it was)."""
    from apps.billing.models import BillingAccount

    billing = _billing(organization)
    billing.status = BillingAccount.EXEMPT if exempt else (
        BillingAccount.ACTIVE if billing.subscription_id else BillingAccount.TRIAL)
    billing.save(update_fields=["status", "updated_at"])
    _record("organization.exempt" if exempt else "organization.exempt_removed", organization)
    _addons_follow(organization)


def set_courtesy(organization, until) -> None:
    """The platform's proxies and agent without charge until `until` (None takes it back)."""
    billing = _billing(organization)
    billing.addons_free_until = until
    billing.save(update_fields=["addons_free_until", "updated_at"])
    _record("organization.courtesy", organization, until=until.isoformat() if until else None)
    _addons_follow(organization)


def _addons_follow(organization) -> None:
    """The free add-ons already on follow the new rule: a courtesy's date, none for a trial or exemption, now when
    they are no longer free (the regular check then switches them off)."""
    from apps.accounts.models import Account
    from apps.billing.services import free_addons, subscribed

    billing = _billing(organization)
    if subscribed(billing):
        return
    free, until, _why = free_addons(billing)
    end = until if free else timezone.now()
    Account._base_manager.filter(organization=organization, platform_proxies=True).update(platform_proxies_ends_at=end)
    Account._base_manager.filter(organization=organization, platform_agent=True).update(platform_agent_ends_at=end)


def set_active(organization, active: bool) -> None:
    """Paused: its agents get no jobs and the platform's proxies and agents it had go back."""
    organization.active = bool(active)
    organization.save(update_fields=["active", "updated_at"])
    _record("organization.resumed" if active else "organization.paused", organization)
    if not active:
        from apps.accounts.platform_agent import end_due as agents_end_due
        from apps.proxy.platform import end_due as proxies_end_due

        proxies_end_due(organization)
        agents_end_due(organization)


def delete_organization(organization) -> dict:
    """A test organization and everything of it: the platform's proxies go back, the platform's agents are removed,
    its accounts (with their worlds, jobs and logs), nodes, presets and own proxies are deleted, and the users who
    belonged only to it are switched off (not deleted). Never the platform's own organization, nor one paying."""
    from apps.accounts import platform_agent
    from apps.accounts.models import Account, DockerHost, Node
    from apps.billing.services import subscribed
    from apps.profiles.models import Preset, Profile
    from apps.proxy import platform as platform_proxies
    from apps.proxy.models import ProxyProfile

    from .models import Membership

    if organization.slug == _default_slug():
        raise ValueError("A organização da plataforma não pode ser apagada.")
    if subscribed(_billing(organization)):
        raise ValueError("Esta organização tem uma assinatura ativa: cancele a assinatura antes.")
    name = organization.name
    accounts = list(Account._base_manager.filter(organization=organization))
    for account in accounts:
        if account.platform_proxies:
            platform_proxies.end(account)
        if account.platform_agent:
            platform_agent.end(account)
    user_ids = list(Membership._base_manager.filter(organization=organization).values_list("user_id", flat=True))
    from apps.audit.services import record, rehome_organization
    from apps.orgs.services import default_organization

    platform = default_organization()
    org_id = str(organization.pk)
    with transaction.atomic():
        Account._base_manager.filter(organization=organization).delete()
        Node._base_manager.filter(organization=organization).delete()
        Preset._base_manager.filter(organization=organization).delete()
        Profile._base_manager.filter(organization=organization).delete()
        ProxyProfile._base_manager.filter(organization=organization, lent_to__isnull=True).delete()
        DockerHost._base_manager.filter(organization=organization).delete()
        Membership._base_manager.filter(organization=organization).delete()
        from django.contrib.auth import get_user_model

        alone = (get_user_model().objects.filter(pk__in=user_ids, is_superuser=False)
                 .exclude(pk__in=Membership._base_manager.values_list("user_id", flat=True)))
        switched_off = alone.update(is_active=False)
        # its history stays, under the platform's organization (the name is in each event's target)
        rehome_organization(organization, platform)
        organization.delete()
        record("organization.deleted", target_label=name, organization=platform,
               details={"organization_id": org_id, "accounts": len(accounts), "users_switched_off": switched_off})
    return {"name": name, "accounts": len(accounts), "users_switched_off": switched_off}
