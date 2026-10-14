"""Billing rules (N-124).

Every organization has a plan row, created the first time it is needed: the default organization is
exempt, any other starts a 7-day trial (4 active sub-accounts). A paid plan covers up to the next due
date; 3 days after it the agents stop receiving jobs (nothing is deleted), 30 days after it the
organization only reads. Paying puts everything back at once.
"""
from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import plans
from .models import BillingAccount, BillingEvent

logger = logging.getLogger(__name__)


@dataclass
class State:
    code: str                 # exempt | trial | active | overdue | paused | read_only
    label: str
    allows_work: bool
    read_only: bool
    limit: int | None         # active sub-accounts allowed; None = no limit
    ends_at: datetime | None  # end of the trial or of the paid period


def account_for(organization) -> BillingAccount | None:
    if organization is None:
        return None
    found = BillingAccount.objects.filter(organization=organization).first()
    if found is not None:
        return found
    from apps.orgs.services import DEFAULT_SLUG

    if organization.slug == DEFAULT_SLUG:
        fields = {"status": BillingAccount.EXEMPT, "limit": 0}
    else:
        start = organization.created_at or timezone.now()
        fields = {"status": BillingAccount.TRIAL, "limit": plans.TRIAL_LIMIT,
                  "trial_ends_at": start + timedelta(days=plans.TRIAL_DAYS)}
    try:
        with transaction.atomic():
            return BillingAccount.objects.create(organization=organization, **fields)
    except IntegrityError:
        return BillingAccount.objects.get(organization=organization)


def state_of(account: BillingAccount | None, now: datetime | None = None) -> State:
    now = now or timezone.now()
    if account is None or account.status == BillingAccount.EXEMPT:
        return State("exempt", "Isenta", True, False, None, None)
    paid = account.paid_until is not None and account.status in (BillingAccount.ACTIVE, BillingAccount.CANCELED)
    end = account.paid_until if paid else account.trial_ends_at
    limit = account.limit if paid else plans.TRIAL_LIMIT
    if end is None or now <= end:
        if paid:
            label = "Cancelada · vale até o fim do período" if account.status == BillingAccount.CANCELED else "Assinatura ativa"
            return State("active", label, True, False, limit, end)
        return State("trial", "Teste grátis", True, False, limit, end)
    late = now - end
    if paid and account.status == BillingAccount.ACTIVE and late <= timedelta(days=plans.GRACE_DAYS):
        return State("overdue", "Pagamento em atraso", True, False, limit, end)
    if late <= timedelta(days=plans.READ_ONLY_DAYS):
        label = "Teste encerrado" if not paid else ("Plano encerrado" if account.status == BillingAccount.CANCELED else "Pausada por falta de pagamento")
        return State("paused", label, False, False, limit, end)
    return State("read_only", "Somente leitura", False, True, limit, end)


def state_for(organization, now=None) -> State:
    return state_of(account_for(organization), now)


def allows_work(organization) -> bool:
    """May this organization's agents receive jobs now? (no organization: the platform's own)."""
    if organization is None:
        return True
    return state_for(organization).allows_work


def active_subaccounts(organization) -> int:
    from apps.accounts.models import GameAccount

    return GameAccount._base_manager.filter(
        account__organization=organization, active=True, blocked=False, account__active=True,
    ).count()


def room_for(organization, more: int = 1) -> bool:
    """Can `more` sub-accounts be turned on without going over the plan?"""
    if organization is None:
        return True
    limit = state_for(organization).limit
    return limit is None or active_subaccounts(organization) + more <= limit


def limit_message(organization) -> str:
    limit = state_for(organization).limit
    return f"Seu plano cobre {limit} subcontas ativas. Pause outra ou suba de faixa em Minha organização > Plano."


# --- Asaas notices -----------------------------------------------------------------------------

def _add_month(day: date) -> date:
    month = day.month % 12 + 1
    year = day.year + (day.month == 12)
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


def _end_of(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(23, 59, 59)))


def _find(obj: dict) -> BillingAccount | None:
    subscription = obj.get("subscription") if obj.get("object") == "payment" else obj.get("id")
    if subscription:
        found = BillingAccount.objects.filter(subscription_id=subscription).first()
        if found:
            return found
    link = obj.get("paymentLink")
    if link:
        found = BillingAccount.objects.filter(link_id=link).first()
        if found:
            return found
    customer = obj.get("customer")
    if customer:
        return BillingAccount.objects.filter(customer_id=customer).first()
    return None


def handle_event(payload: dict) -> str:
    """Apply one notice once. Returns what happened, for the log."""
    event_id = str(payload.get("id") or "")
    event = str(payload.get("event") or "")
    obj = payload.get("payment") or payload.get("subscription") or {}
    if not event_id or not event:
        return "ignored"
    keep = {key: obj.get(key) for key in ("object", "id", "subscription", "paymentLink", "customer", "value",
                                          "status", "dueDate", "billingType") if obj.get(key) is not None}
    with transaction.atomic():
        try:
            record = BillingEvent.objects.create(event_id=event_id[:120], event=event[:64], data=keep)
        except IntegrityError:
            return "duplicate"
        account = _find(obj)
        if account is None:
            return "unknown"
        account = BillingAccount.objects.select_for_update().get(pk=account.pk)
        record.organization_id = account.organization_id
        record.save(update_fields=["organization"])
        result = _apply(account, event, obj)
    logger.info("Asaas %s for organization %s: %s", event, account.organization_id, result)
    return result


def _apply(account: BillingAccount, event: str, obj: dict) -> str:
    fields = []
    if obj.get("customer") and account.customer_id != obj["customer"]:
        account.customer_id = obj["customer"]
        fields.append("customer_id")
    subscription = obj.get("subscription") if obj.get("object") == "payment" else obj.get("id")
    if subscription and event.startswith(("PAYMENT_", "SUBSCRIPTION_CREATED", "SUBSCRIPTION_UPDATED")) and account.subscription_id != subscription:
        account.subscription_id = subscription
        fields.append("subscription_id")
        if account.link_limit:
            account.limit, account.price = account.link_limit, plans.price_for(account.link_limit)
            fields += ["limit", "price"]
    result = "linked" if fields else "seen"

    if event in ("PAYMENT_CONFIRMED", "PAYMENT_RECEIVED"):
        due = _parse_day(obj.get("dueDate")) or timezone.localdate()
        until = _end_of(_add_month(due))
        if account.paid_until is None or until > account.paid_until:
            account.paid_until = until
            fields.append("paid_until")
        if account.status != BillingAccount.ACTIVE:
            account.status = BillingAccount.ACTIVE
            fields.append("status")
        result = "paid"
        _notify(account, f"✅ IkaHub: pagamento confirmado. Plano ativo até {timezone.localtime(account.paid_until):%d/%m/%Y}.")
    elif event == "PAYMENT_OVERDUE":
        result = "overdue"
        _notify(account, "⚠️ IkaHub: o pagamento do plano venceu. Os jobs pausam em 3 dias se ele não for pago. "
                         "Veja em Minha organização > Plano.")
    elif event == "PAYMENT_REFUNDED":
        due = _parse_day(obj.get("dueDate"))
        if due and account.paid_until and account.paid_until > _end_of(due):
            account.paid_until = _end_of(due)
            fields.append("paid_until")
        result = "refunded"
    elif event in ("SUBSCRIPTION_DELETED", "SUBSCRIPTION_INACTIVATED"):
        if account.status == BillingAccount.ACTIVE:
            account.status = BillingAccount.CANCELED
            fields.append("status")
        result = "canceled"
    if fields:
        account.save(update_fields=list(dict.fromkeys(fields)) + ["updated_at"])
    return result


def _parse_day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _notify(account: BillingAccount, text: str) -> None:
    """The organization's own Telegram chat, when it has one. Never breaks the notice."""
    try:
        from apps.telegram.services.bot_api import send_message
        from apps.telegram.services.config import organization_config, platform_bot

        if not platform_bot().is_active:
            return
        config = organization_config(account.organization)
        if config is not None and config.chat_id:
            send_message(config.chat_id, text)
    except Exception:  # noqa: BLE001
        logger.warning("Billing notice to Telegram failed for organization %s", account.organization_id)
