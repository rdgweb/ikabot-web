"""Organization scope (N-106, stage 3a): a person only sees the data of their organization.

The filter lives in the data access, not in each view: the default manager of every model that
holds customer data narrows itself to the organization of the request. To keep queries cheap (and
row locks small) each model is narrowed by its nearest direct link — the organization column, or
the lists of accounts, game accounts, nodes or presets of the organization — instead of joining
up to the organization table.

No scope (commands, the agent API, the Telegram webhook, anonymous pages) means no filter: the
agent API gets its own scope by node in stage 3b. A logged person without an organization sees
nothing.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field

from django.db import models
from django.db.models import Q

# model -> (lookup up to the anchor, anchor, rows without the link are shared by everybody)
SCOPES: dict[str, tuple[str, str, bool]] = {
    # the organization column itself
    "accounts.Node": ("organization", "org", False),
    "accounts.DockerHost": ("organization", "org", False),
    "accounts.Account": ("organization", "org", False),
    "profiles.Profile": ("organization", "org", False),
    "profiles.Preset": ("organization", "org", False),
    "proxy.ProxyProfile": ("organization", "org", False),
    # by GameForge account
    "accounts.GameAccount": ("account", "account", False),
    "jobs.Workflow": ("account", "account", False),
    "jobs.Job": ("account", "account", False),
    "jobs.ConstructionResourceReservation": ("account", "account", False),
    "game.AccountSnapshot": ("account", "account", False),
    "game.AccountSnapshotHistory": ("account", "account", False),
    "worldintel.WorldDump": ("account", "account", False),
    "market.InternalMarketOrder": ("buyer_account", "account", False),
    "market.ConstructionMarketIntervention": ("account", "account", False),
    "proxy.AccountProxyReservation": ("account", "account", False),
    "jobs.WorkflowRun": ("workflow__account", "account", False),
    "jobs.JobLog": ("job__account", "account", False),
    "worldintel.WorldDumpIsland": ("dump__account", "account", False),
    "worldintel.WorldDumpCity": ("dump__account", "account", False),
    "worldintel.WorldDumpPlayer": ("dump__account", "account", False),
    # by game account (sub-account)
    "diplomacy.DiplomacyMessage": ("game_account", "game_account", False),
    "espionage.SpyReport": ("game_account", "game_account", False),
    "espionage.RaidAlertSent": ("game_account", "game_account", False),
    "combat.CombatReport": ("game_account", "game_account", False),
    "combat.BattleIntel": ("report__game_account", "game_account", False),
    "combat.SimulationCheck": ("report__game_account", "game_account", True),     # checks without a report: the simulator's own
    "telegram.TelegramAccountConfig": ("game_account", "game_account", False),
    "telegram.DigestEntry": ("game_account", "game_account", False),
    "telegram.NotificationTemplate": ("game_account", "game_account", True),      # templates without account: the defaults
    "telegram.MessageAudit": ("game_account", "game_account", True),              # bot messages not about an account
    "market.BlackMarketOffer": ("game_account", "game_account", False),
    "market.BlackMarketUnitQuote": ("game_account", "game_account", False),
    "market.BlackMarketAvailableOffer": ("game_account", "game_account", False),
    "market.PublicMarketScan": ("game_account", "game_account", False),
    "market.PublicMarketOffer": ("scan__game_account", "game_account", False),
    "captcha.CaptchaChallenge": ("game_account", "game_account", False),
    "generals_bank.GeneralsBankConfig": ("bank_game_account", "game_account", False),
    "generals_bank.GeneralsBankProducer": ("producer_game_account", "game_account", False),
    "generals_bank.GeneralsBankCycleTask": ("producer_game_account", "game_account", False),
    "generals_bank.GeneralsBankCycle": ("bank_config__bank_game_account", "game_account", False),
    "generals_bank.GeneralsBankTransaction": ("cycle__bank_config__bank_game_account", "game_account", False),
    # by node
    "accounts.AgentUpdateRequest": ("node", "node", False),
    "manual_control.ManualControlSession": ("node", "node", False),
    "manual_control.HeldJob": ("session__node", "node", False),
    # by preset
    "profiles.PresetGameAccount": ("preset", "preset", False),
    "profiles.PresetAction": ("preset", "preset", False),
}

# Platform-wide for now (per-organization settings are stage 4).
GLOBAL_MODELS = {
    "combat.SimulatorCalibration", "telegram.TelegramBotConfig", "telegram.TelegramIncomingCommand", "captcha.CaptchaConfig",
    "website.InviteRequest",
    "billing.BillingAccount", "billing.BillingEvent",
    "accounts.AgentProvisionRequest",   # the platform's own work orders to its supervisor (N-131)   # read by the organization's own id only (N-124)    # the public site's invitation requests: the platform's own
}


@dataclass
class Scope:
    organization: object | None          # None: logged in without organization, sees nothing
    _ids: dict = field(default_factory=dict)

    def ids(self, anchor: str) -> list:
        if anchor not in self._ids:
            from apps.accounts.models import Account, GameAccount, Node
            from apps.profiles.models import Preset

            org = self.organization
            source = {
                "account": lambda: Account._base_manager.filter(organization=org),
                "game_account": lambda: GameAccount._base_manager.filter(account__organization=org),
                "node": lambda: Node._base_manager.filter(organization=org),
                "preset": lambda: Preset._base_manager.filter(organization=org),
            }[anchor]
            self._ids[anchor] = list(source().values_list("pk", flat=True))
        return self._ids[anchor]

    def apply(self, queryset):
        rule = SCOPES.get(queryset.model._meta.label)
        if rule is None:
            return queryset
        if self.organization is None:
            return queryset.none()
        lookup, anchor, shared_when_empty = rule
        condition = Q(**{lookup: self.organization}) if anchor == "org" else Q(**{f"{lookup}__in": self.ids(anchor)})
        if shared_when_empty:
            condition |= Q(**{f"{lookup}__isnull": True})
        return queryset.filter(condition)


_current: contextvars.ContextVar[Scope | None] = contextvars.ContextVar("org_scope", default=None)


def current() -> Scope | None:
    return _current.get()


def current_organization():
    scope = _current.get()
    return scope.organization if scope else None


def activate(organization, *, deny: bool = False):
    """Narrow the data access to an organization until the token is reset."""
    return _current.set(Scope(None if deny else organization))


def deactivate(token) -> None:
    _current.reset(token)


class unscoped:
    """with unscoped(): the platform's own work inside a person's request (rare and explicit)."""

    def __enter__(self):
        self._token = _current.set(None)

    def __exit__(self, *exc):
        _current.reset(self._token)


class OrgScopedQuerySet(models.QuerySet):
    """Narrows itself again whenever it is copied inside a scope.

    Forms and filters build their choices (ModelChoiceField, ModelChoiceFilter) when the module
    loads, outside any request; Django copies that queryset with .all() for each form, inside the
    request. Applying the scope on every copy is what keeps those choice lists from showing other
    organizations' accounts and nodes.
    """

    _scoped_for = None

    def _clone(self):
        clone = super()._clone()
        clone._scoped_for = self._scoped_for
        return clone

    def _chain(self):
        clone = super()._chain()
        scope = _current.get()
        if scope is not None and clone._scoped_for is not scope:
            clone._scoped_for = scope                  # set first: apply() chains again
            clone = scope.apply(clone)
        return clone

    def _fetch_all(self):
        scope = _current.get()
        if self._result_cache is None and scope is not None and self._scoped_for is not scope:
            narrowed = self._chain()                   # a queryset made outside the request, evaluated inside it
            narrowed._fetch_all()
            self._result_cache, self._prefetch_done = narrowed._result_cache, narrowed._prefetch_done
            return
        super()._fetch_all()

    def count(self):
        if self._result_cache is None and _current.get() is not None and self._scoped_for is not _current.get():
            return self._chain().count()
        return super().count()

    def exists(self):
        if self._result_cache is None and _current.get() is not None and self._scoped_for is not _current.get():
            return self._chain().exists()
        return super().exists()


class OrgScopedManager(models.Manager.from_queryset(OrgScopedQuerySet)):
    def get_queryset(self):
        queryset = super().get_queryset()
        scope = _current.get()
        if scope is None:
            return queryset
        queryset._scoped_for = scope
        return scope.apply(queryset)


def organization_for(user):
    """The organization a logged person works in (the first one; choosing comes with invitations)."""
    membership = user.memberships.select_related("organization").order_by("created_at").first()
    return membership.organization if membership else None
