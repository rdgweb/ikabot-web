"""Asaas API (N-124): the few calls the hub makes. The key lives only in the environment."""
from __future__ import annotations

import logging
from decimal import Decimal

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

PRODUCTION = "https://api.asaas.com/v3"
SANDBOX = "https://api-sandbox.asaas.com/v3"
TIMEOUT = 15


class AsaasError(Exception):
    """The Asaas refused or could not be reached; the message is safe to show (no key, no payload)."""


def configured() -> bool:
    return bool(settings.ASAAS_API_KEY)


def _base() -> str:
    return SANDBOX if settings.ASAAS_SANDBOX else PRODUCTION


def _call(method: str, path: str, body: dict | None = None) -> dict:
    if not configured():
        raise AsaasError("A cobrança ainda não está configurada.")
    headers = {"access_token": settings.ASAAS_API_KEY, "Content-Type": "application/json", "User-Agent": "IkaHub"}
    try:
        response = requests.request(method, f"{_base()}{path}", json=body, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as exc:
        logger.warning("Asaas %s %s unreachable: %s", method, path, type(exc).__name__)
        raise AsaasError("O Asaas não respondeu. Tente de novo em instantes.") from exc
    if response.status_code >= 400:
        try:
            errors = [e.get("description", "") for e in response.json().get("errors", [])]
        except ValueError:
            errors = []
        logger.warning("Asaas %s %s refused (%s): %s", method, path, response.status_code, "; ".join(errors)[:300])
        raise AsaasError(errors[0] if errors else f"O Asaas recusou o pedido ({response.status_code}).")
    try:
        return response.json() if response.content else {}
    except ValueError:
        return {}


def create_payment_link(*, organization, limit: int, value: Decimal, success_url: str) -> tuple[str, str]:
    """A recurring monthly link for this organization's plan. Returns (id, url)."""
    data = _call("POST", "/paymentLinks", {
        "name": f"IkaHub — até {limit} subcontas",
        "description": f"Assinatura mensal do IkaHub para {organization.name}: até {limit} subcontas ativas.",
        "billingType": "UNDEFINED",
        "chargeType": "RECURRENT",
        "subscriptionCycle": "MONTHLY",
        "value": float(value),
        "dueDateLimitDays": 3,
        "notificationEnabled": True,
        "externalReference": str(organization.pk),
        "callback": {"successUrl": success_url, "autoRedirect": True},
    })
    return str(data.get("id") or ""), str(data.get("url") or "")


def delete_payment_link(link_id: str) -> None:
    if link_id:
        _call("DELETE", f"/paymentLinks/{link_id}")


def update_subscription_value(subscription_id: str, value: Decimal, description: str) -> None:
    _call("POST", f"/subscriptions/{subscription_id}", {
        "value": float(value), "description": description, "updatePendingPayments": True,
    })


def cancel_subscription(subscription_id: str) -> None:
    _call("DELETE", f"/subscriptions/{subscription_id}")


def create_webhook(*, url: str, token: str, email: str) -> str:
    data = _call("POST", "/webhooks", {
        "name": "IkaHub", "url": url, "email": email, "enabled": True, "interrupted": False,
        "apiVersion": 3, "authToken": token, "sendType": "SEQUENTIALLY",
        "events": [
            "PAYMENT_CREATED", "PAYMENT_CONFIRMED", "PAYMENT_RECEIVED", "PAYMENT_OVERDUE", "PAYMENT_REFUNDED",
            "PAYMENT_DELETED", "SUBSCRIPTION_CREATED", "SUBSCRIPTION_UPDATED", "SUBSCRIPTION_INACTIVATED",
            "SUBSCRIPTION_DELETED",
        ],
    })
    return str(data.get("id") or "")
