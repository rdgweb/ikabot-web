"""Recording audit events (N-120).

Inside a request the events are kept and written when the response leaves without a server error,
the same action on many rows merged into one event with the count. Outside a request (commands,
the agent's own work) only explicit calls record, and they write at once.
A failure here never breaks what the person was doing.
"""
from __future__ import annotations

import contextvars
import ipaddress
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

RETENTION_DAYS = getattr(settings, "AUDIT_RETENTION_DAYS", 365)

# action -> (category, what the screen says)
ACTIONS = {
    "login": ("acesso", "Entrou"),
    "logout": ("acesso", "Saiu"),
    "login_failed": ("acesso", "Tentativa de login falhou"),
    "account.created": ("contas", "Cadastrou conta do GameForge"),
    "account.updated": ("contas", "Alterou conta do GameForge"),
    "account.deleted": ("contas", "Apagou conta do GameForge"),
    "gameaccount.updated": ("contas", "Alterou subconta"),
    "gameaccount.deleted": ("contas", "Apagou subconta"),
    "node.created": ("nos", "Cadastrou nó"),
    "node.updated": ("nos", "Alterou nó"),
    "node.deleted": ("nos", "Apagou nó"),
    "dockerhost.created": ("nos", "Cadastrou máquina"),
    "dockerhost.updated": ("nos", "Alterou máquina"),
    "dockerhost.deleted": ("nos", "Apagou máquina"),
    "proxyprofile.created": ("proxies", "Cadastrou proxy"),
    "proxyprofile.updated": ("proxies", "Alterou proxy"),
    "proxyprofile.deleted": ("proxies", "Apagou proxy"),
    "preset.created": ("jobs", "Criou preset"),
    "preset.updated": ("jobs", "Alterou preset"),
    "preset.deleted": ("jobs", "Apagou preset"),
    "job.created": ("jobs", "Criou job"),
    "job.cancelled": ("jobs", "Cancelou job"),
    "workflow.created": ("jobs", "Criou workflow"),
    "workflow.deleted": ("jobs", "Apagou workflow"),
    "control.opened": ("controle", "Assumiu o controle manual"),
    "control.ended": ("controle", "Controle manual encerrado"),
    "setting.changed": ("config", "Alterou configuração"),
    "telegram.changed": ("config", "Alterou o Telegram"),
    "captcha.changed": ("config", "Alterou o captcha"),
    "user.created": ("usuarios", "Criou usuário"),
    "user.updated": ("usuarios", "Alterou usuário"),
    "user.password": ("usuarios", "Trocou senha"),
    "user.deleted": ("usuarios", "Apagou usuário"),
    "membership.changed": ("usuarios", "Mudou papel"),
    "credentials.delivered": ("credenciais", "Senhas entregues ao agente"),
    "invite.created": ("usuarios", "Criou convite"),
    "invite.accepted": ("usuarios", "Aceitou convite e os termos"),
    "invite.requested": ("usuarios", "Pediu convite pelo site"),
    "billing.checkout": ("plano", "Abriu o pagamento do plano"),
    "billing.changed": ("plano", "Trocou a faixa do plano"),
    "billing.canceled": ("plano", "Cancelou a assinatura"),
}
CATEGORIES = {
    "acesso": "Acesso", "contas": "Contas", "nos": "Nós e máquinas", "proxies": "Proxies", "jobs": "Jobs",
    "controle": "Controle manual", "config": "Configurações", "usuarios": "Usuários", "credenciais": "Credenciais", "plano": "Plano",
}


@dataclass
class _Context:
    request: object
    events: list = field(default_factory=list)


_current: contextvars.ContextVar[_Context | None] = contextvars.ContextVar("audit_context", default=None)


def begin(request):
    return _current.set(_Context(request=request))


def finish(token, *, write: bool) -> None:
    context = _current.get()
    _current.reset(token)
    if write and context and context.events:
        _write(_merge(context.events))


def current_request():
    context = _current.get()
    return context.request if context else None


def acting_user():
    """The person behind the current web request (never the agent API)."""
    request = current_request()
    if request is None or request.path.startswith("/api/"):
        return None
    user = getattr(request, "user", None)
    return user if getattr(user, "is_authenticated", False) else None


def client_ip(request) -> str | None:
    raw = (request.META.get("HTTP_X_REAL_IP") or request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0]
           or request.META.get("REMOTE_ADDR") or "").strip()
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


def organization_of(target, user=None):
    """Whose event it is: the target's organization, its account's, the person's, or the default one."""
    for path in ("organization", "account.organization", "node.organization", "game_account.account.organization"):
        value = target
        for part in path.split("."):
            value = getattr(value, part, None) if value is not None else None
        if value is not None and hasattr(value, "slug"):
            return value
    if user is not None and getattr(user, "pk", None):
        membership = user.memberships.select_related("organization").first() if hasattr(user, "memberships") else None
        if membership:
            return membership.organization
    from apps.orgs.services import default_organization

    return default_organization()


def record(action: str, *, target=None, target_label: str = "", user=None, actor_kind: str | None = None,
           actor_label: str = "", result: str = "ok", details: dict | None = None, organization=None) -> None:
    """Record one event. Inside a request it waits for the response; outside it writes now."""
    try:
        request = current_request()
        if user is None and actor_kind is None:
            user = acting_user()
        kind = actor_kind or ("user" if user is not None else "system")
        event = {
            "action": action,
            "category": ACTIONS.get(action, ("outros", action))[0],
            "user": user if kind == "user" else None,
            "actor_kind": kind,
            "actor_label": actor_label or (getattr(user, "get_username", lambda: "")() if user is not None else ""),
            "target_type": type(target).__name__.lower() if target is not None else "",
            "target_id": str(getattr(target, "pk", "") or "") if target is not None else "",
            "target_label": (target_label or (str(target) if target is not None else ""))[:200],
            "organization": organization or organization_of(target, user),
            "ip": client_ip(request) if request is not None else None,
            "user_agent": (request.META.get("HTTP_USER_AGENT", "") if request is not None else "")[:200],
            "result": result,
            "details": details or {},
        }
        context = _current.get()
        if context is not None:
            context.events.append(event)
        else:
            _write([dict(event, count=1)])
    except Exception:  # noqa: BLE001 - the audit never breaks the action
        logger.exception("Audit event %s could not be recorded", action)


def _merge(events: list[dict]) -> list[dict]:
    merged: dict[tuple, dict] = {}
    for event in events:
        key = (event["action"], event["actor_kind"], getattr(event["user"], "pk", None), event["result"])
        if key not in merged:
            merged[key] = dict(event, count=1, _ids={event["target_id"]})
            continue
        first = merged[key]
        if event["target_id"] in first["_ids"]:
            continue                               # the same row saved twice in one request
        first["_ids"].add(event["target_id"])
        first["count"] += 1
        first["target_label"] = f"{first['target_label'].split(' e mais ')[0]} e mais {first['count'] - 1}"
    for event in merged.values():
        event.pop("_ids")
    return list(merged.values())


def _write(events: list[dict]) -> None:
    from .models import AuditEvent

    try:
        AuditEvent.objects.bulk_create([
            AuditEvent(
                organization=event["organization"], actor=event["user"], actor_label=event["actor_label"][:150],
                actor_kind=event["actor_kind"], action=event["action"], category=event["category"],
                target_type=event["target_type"][:64], target_id=event["target_id"][:64], target_label=event["target_label"],
                count=event["count"], ip=event["ip"], user_agent=event["user_agent"], result=event["result"],
                details=event["details"],
            )
            for event in events
        ])
        purge_if_due()
    except Exception:  # noqa: BLE001
        logger.exception("Audit events could not be written")


def purge_if_due() -> int:
    """Once a day (any process), drop what is older than the retention."""
    if not cache.add(f"audit:purge:{timezone.localdate():%Y%m%d}", 1, timeout=26 * 3600):
        return 0
    from .models import AuditEvent

    removed = AuditEvent.objects.purge_before(timezone.now() - timedelta(days=RETENTION_DAYS))
    if removed:
        logger.info("Audit retention: %s events older than %s days removed", removed, RETENTION_DAYS)
    return removed


def once_per(key: str, seconds: int) -> bool:
    """True the first time in the window (credentials delivered to an agent: once an hour per node)."""
    try:
        return bool(cache.add(f"audit:once:{key}", 1, timeout=seconds))
    except Exception:  # noqa: BLE001 - without cache, record every time
        return True
