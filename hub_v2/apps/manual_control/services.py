"""State of the manual control of a game account (N-72).

One place decides whether the automation of an account may act:

    hold_job()          asked when a job is about to start
    request_control()   the operator clicked "Assumir controle"
    advance()           the preparation page asking "is it ready?"
    touch()             a sign that the operator's tab is still there
    end()               the tab closed, the lease ran out, or the operator ended it
    sweep_expired()     ends what nobody ended

Nothing here needs a scheduler: a lease that ran out is noticed by the next job
that tries to start, by the next agent heartbeat or by the next look at the page.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import GameAccount
from apps.jobs.models import Job, JobLog
from apps.settings_app.utils import get_int_setting

from . import rpc
from .models import HeldJob, ManualControlSession

logger = logging.getLogger(__name__)

# First agent version that answers manual-control requests.
MIN_AGENT_VERSION = (0, 1, 91)

# The preparation page asks every couple of seconds; if it stops, the request is dropped.
PAUSE_LEASE = timedelta(seconds=45)
# How long the request waits for the job that is running to finish by itself.
PAUSE_TIMEOUT = timedelta(minutes=10)
# How long the agent has to open the game session (it may have to log in).
OPEN_TIMEOUT = timedelta(seconds=150)
# After the tab says it is leaving. A page change inside the game also says so, and
# the next page takes the lease back; a closed tab does not.
CLOSING_GRACE = timedelta(seconds=12)
# Held jobs go back to the agent a few seconds apart, in the order they were due.
RESUME_FIRST_DELAY = 5
RESUME_SPACING = 4
# A release that died halfway is finished by the next sweep.
RESUMING_TIMEOUT = timedelta(minutes=2)

HELD_MESSAGE = (
    "Conta em controle manual: job em espera, sem começar. Volta sozinho quando o controle manual terminar."
)


def _now():
    return timezone.now()


def _lease() -> timedelta:
    return timedelta(seconds=max(30, get_int_setting("manual_control_lease_seconds", 60)))


def _idle_limit() -> timedelta | None:
    minutes = get_int_setting("manual_control_idle_minutes", 30)
    return timedelta(minutes=minutes) if minutes > 0 else None


def _hash(value: str) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()


# -- where the session is served ---------------------------------------------------

def gateway_domain() -> str:
    return str(getattr(settings, "MANUAL_GATEWAY_DOMAIN", "") or "").strip().strip(".").lower()


def gateway_enabled() -> bool:
    return bool(gateway_domain())


def session_host(session: ManualControlSession) -> str:
    return f"{session.token}.{gateway_domain()}"


def session_origin(session: ManualControlSession) -> str:
    scheme = str(getattr(settings, "MANUAL_GATEWAY_SCHEME", "") or "https")
    port = str(getattr(settings, "MANUAL_GATEWAY_PORT", "") or "").strip()
    return f"{scheme}://{session_host(session)}" + (f":{port}" if port else "")


# -- reading the state -------------------------------------------------------------

def open_session_for(game_account_id) -> ManualControlSession | None:
    if not game_account_id:
        return None
    return ManualControlSession.objects.filter(open_key=str(game_account_id)).first()


def control_state(game_account_id) -> str:
    """automation | pausing | manual | resuming"""
    session = open_session_for(game_account_id)
    if session is None:
        return "automation"
    return {"pausing": "pausing", "active": "manual", "resuming": "resuming"}.get(session.status, "automation")


def running_jobs(game_account_id):
    """Jobs of the account that an agent is really executing right now."""
    now = _now()
    return (
        Job.objects.filter(game_account_id=game_account_id, status="running")
        .exclude(lease_expires_at__lt=now)
        .order_by("started_at")
    )


def _version_tuple(raw: str) -> tuple[int, ...]:
    parts = []
    for piece in str(raw or "").strip().lstrip("v").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def agent_supports(node) -> bool:
    return _version_tuple(getattr(node, "agent_version", "")) >= MIN_AGENT_VERSION


# -- the gate ----------------------------------------------------------------------

def hold_job(job: Job) -> bool:
    """True when ``job`` must not start now because its account is not in automation.

    The job is left as it is and remembered, so the release hands it back.
    """
    if not job.game_account_id:
        return False
    session = open_session_for(job.game_account_id)
    if session is None:
        return False
    if _is_expired(session):
        end(session, "expired")
        return False
    held, created = HeldJob.objects.get_or_create(job=job, defaults={"session": session})
    if not created and held.session_id != session.pk:
        HeldJob.objects.filter(pk=held.pk).update(session=session)
    if created:
        try:
            JobLog.objects.create(job=job, level="info", message=HELD_MESSAGE)
        except Exception:  # noqa: BLE001 - a log line never decides whether a job is held
            logger.debug("Could not log the hold of job %s", job.pk, exc_info=True)
    return True


# -- taking control ----------------------------------------------------------------

class ControlRefused(Exception):
    """The request cannot be accepted; the message is for the operator."""


def request_control(game_account: GameAccount, user) -> ManualControlSession:
    if not gateway_enabled():
        raise ControlRefused("O controle manual ainda não está configurado neste hub (domínio do gateway).")
    if not game_account.active:
        raise ControlRefused("Esta conta de jogo está inativa.")
    node = game_account.account.node
    if node is None:
        raise ControlRefused("A conta não está ligada a nenhum nó.")

    sweep_expired(game_account_id=game_account.pk)
    existing = open_session_for(game_account.pk)
    if existing is not None:
        if existing.user_id and user is not None and existing.user_id != user.pk:
            raise ControlRefused("Esta conta já está em controle manual por outro usuário.")
        return existing

    if not node.is_online:
        raise ControlRefused(f"O agente do nó {node.name} está offline; sem ele não há como abrir o jogo.")
    if not agent_supports(node):
        raise ControlRefused(
            f"O agente do nó {node.name} (versão {node.agent_version or '?'}) ainda não atende controle manual; "
            "atualize o agente."
        )

    now = _now()
    try:
        with transaction.atomic():
            return ManualControlSession.objects.create(
                game_account=game_account,
                node=node,
                user=user if getattr(user, "pk", None) else None,
                status=ManualControlSession.PAUSING,
                open_key=str(game_account.pk),
                token=secrets.token_hex(16),
                expires_at=now + PAUSE_LEASE,
                last_seen_at=now,
            )
    except IntegrityError:
        # two clicks at once: the unique open_key let only one in
        existing = open_session_for(game_account.pk)
        if existing is None:
            raise ControlRefused("Não foi possível iniciar o controle manual; tente de novo.") from None
        return existing


def advance(session_id) -> dict:
    """One step of the preparation; what the preparation page shows."""
    with transaction.atomic():
        session = ManualControlSession.objects.select_for_update().filter(pk=session_id).first()
        if session is None:
            return {"state": "ended", "reason": "missing", "error": ""}
        if session.status != ManualControlSession.PAUSING:
            return describe(session)

        now = _now()
        if now - session.created_at > PAUSE_TIMEOUT:
            outcome = ("error", "A automação em execução não terminou a tempo; o controle manual não foi aberto.")
        else:
            outcome = None
            session.expires_at = now + PAUSE_LEASE
            session.last_seen_at = now
            running = list(running_jobs(session.game_account_id)[:3])
            if running:
                session.save(update_fields=["expires_at", "last_seen_at", "updated_at"])
                return {"state": "pausing", "step": "waiting_job", "jobs": [_job_brief(job, now) for job in running]}

            if not session.open_request_id:
                session.open_request_id = rpc.send(
                    session.node_id, _agent_payload(session, "open"), ttl=int(OPEN_TIMEOUT.total_seconds()),
                )
                session.open_requested_at = now
                session.save(update_fields=["expires_at", "last_seen_at", "open_request_id", "open_requested_at", "updated_at"])
                return {"state": "pausing", "step": "opening"}

            answer = rpc.poll(session.open_request_id)
            if answer is None:
                if now - (session.open_requested_at or now) > OPEN_TIMEOUT:
                    outcome = ("error", "O agente não respondeu ao pedido de abrir a sessão do jogo.")
                else:
                    session.save(update_fields=["expires_at", "last_seen_at", "updated_at"])
                    return {"state": "pausing", "step": "opening"}
            elif answer.get("ok"):
                session.status = ManualControlSession.ACTIVE
                session.server_host = str(answer.get("host") or "")[:120]
                _record_proxy(session, answer.get("proxy"))
                session.activated_at = now
                session.last_request_at = now
                session.expires_at = now + _lease()
                session.open_request_id = ""
                session.save()
                logger.info("Manual control active: session=%s game_account=%s", session.pk, session.game_account_id)
                return describe(session)
            elif answer.get("retry"):
                # the agent was still busy with the account: ask again on the next step
                session.open_request_id = ""
                session.open_requested_at = None
                session.save(update_fields=["expires_at", "last_seen_at", "open_request_id", "open_requested_at", "updated_at"])
                return {"state": "pausing", "step": "opening"}
            else:
                outcome = ("error", str(answer.get("error") or "O agente nao conseguiu abrir a sessao do jogo.")[:500])

    end(session, outcome[0], error=outcome[1])
    session.refresh_from_db()
    return describe(session)


def _agent_payload(session: ManualControlSession, op: str) -> dict:
    return {
        "op": op,
        "session": str(session.pk),
        "account_id": str(session.game_account.account_id),
        "game_account_id": str(session.game_account_id),
    }


def _job_brief(job: Job, now) -> dict:
    from core.actions.registry import get_action_info

    info = get_action_info(job.action_code) or {}
    started = job.started_at or now
    return {
        "id": str(job.pk),
        "name": str(info.get("name") or info.get("label") or f"ação {job.action_code}"),
        "seconds": max(0, int((now - started).total_seconds())),
    }


def describe(session: ManualControlSession) -> dict:
    if session.status == ManualControlSession.PAUSING:
        return {"state": "pausing", "step": "opening" if session.open_request_id else "waiting_job"}
    if session.status == ManualControlSession.ACTIVE:
        return {"state": "active", "proxy": proxy_summary(session)}
    if session.status == ManualControlSession.RESUMING:
        return {"state": "resuming"}
    return {"state": "ended", "reason": session.end_reason, "error": session.error}


# -- which proxy the session leaves through (N-99) ------------------------------------

def proxy_fingerprint(host: str, port) -> str:
    """Same as the agent computes: names a proxy without its address travelling anywhere."""
    return hashlib.sha256(f"{str(host).strip().lower()}:{port}".encode()).hexdigest()


def _record_proxy(session: ManualControlSession, facts) -> None:
    """Turn what the agent said about the session's proxy into the hub's own terms."""
    from urllib.parse import urlparse

    from apps.proxy.models import AccountProxyReservation, ProxyProfile

    session.proxy_label, session.proxy_scope, session.exit_check = "", "", ""
    if not isinstance(facts, dict):
        return                                   # an agent that does not say (older version)
    if not facts.get("used"):
        session.proxy_scope = "none"
        return
    fingerprint = str(facts.get("fingerprint") or "")
    reserved = set(
        AccountProxyReservation.objects.filter(account_id=session.game_account.account_id).values_list("proxy_profile_id", flat=True)
    )
    session.proxy_scope = "unknown"
    for proxy in ProxyProfile.objects.only("id", "address", "port", "assigned_node_id"):
        if proxy_fingerprint(proxy.address, proxy.port) != fingerprint:
            continue
        session.proxy_label = f"P{proxy.pk}"
        if proxy.pk in reserved:
            session.proxy_scope = "account"
        elif proxy.assigned_node_id == session.node_id:
            session.proxy_scope = "node"
        else:
            session.proxy_scope = "other"
        break
    else:
        # a proxy typed by hand on the node, outside the proxy list
        raw = str(getattr(session.node, "proxy", "") or "").strip()
        if raw:
            parsed = urlparse(raw if "://" in raw else "http://" + raw)
            if proxy_fingerprint(parsed.hostname or "", parsed.port or 0) == fingerprint:
                session.proxy_scope = "node"
    if not facts.get("checked"):
        session.exit_check = "unchecked"
    elif facts.get("is_direct"):
        session.exit_check = "direct"
    elif facts.get("matches_proxy"):
        session.exit_check = "ok"
    else:
        session.exit_check = "differs"


def proxy_summary(session: ManualControlSession) -> dict:
    """{"level": ok|warn|bad|info, "text": for the operator, "short": for the tag inside the game}."""
    scope, label, check = session.proxy_scope, session.proxy_label, session.exit_check
    if not scope:
        return {"level": "info", "short": "", "text": "O agente não informou por qual proxy a sessão sai (versão antiga do agente)."}
    if scope == "none":
        return {"level": "bad", "short": "SEM PROXY",
                "text": "SEM PROXY: esta sessão está saindo pelo endereço do próprio agente, não por um proxy."}
    where = {
        "account": f"Saindo pelo proxy {label}, reservado para esta conta.",
        "node": f"Saindo pelo proxy do nó{' (' + label + ')' if label else ''}, não por um proxy reservado para esta conta.",
        "other": f"Saindo pelo proxy {label}, que não é um dos reservados para esta conta.",
        "unknown": "Saindo por um proxy que o hub não reconhece na lista de proxies.",
    }[scope]
    short = f"proxy {label}" if label else "proxy do nó" if scope == "node" else "proxy desconhecido"
    level = "ok" if scope == "account" else "warn"
    if check == "direct":
        return {"level": "bad", "short": short + " SEM EFEITO",
                "text": where + " Mas o endereço de saída conferido é o do próprio agente: o proxy não está valendo."}
    if check == "ok":
        return {"level": level, "short": short + (" ✓" if level == "ok" else ""), "text": where + " Endereço de saída conferido."}
    if check == "differs":
        return {"level": level, "short": short,
                "text": where + " O endereço de saída é diferente do endereço do proxy (proxy com saída própria), e não é o do agente."}
    return {"level": level, "short": short, "text": where + " Endereço de saída não conferido (serviço de consulta sem resposta)."}


# -- the browser side --------------------------------------------------------------

def find_by_token(token: str) -> ManualControlSession | None:
    token = str(token or "").strip().lower()
    if not token or len(token) > 40:
        return None
    return ManualControlSession.objects.select_related("game_account").filter(token=token).first()


def new_start_url(session: ManualControlSession) -> str:
    """A one-time address that lets one browser into the session."""
    key = secrets.token_urlsafe(32)
    ManualControlSession.objects.filter(pk=session.pk).update(start_key_hash=_hash(key))
    return f"{session_origin(session)}/__ikabot/start/{key}"


def consume_start_key(session: ManualControlSession, key: str) -> str | None:
    """Trade the one-time key for the cookie value; None when the key is not the current one."""
    with transaction.atomic():
        current = ManualControlSession.objects.select_for_update().filter(pk=session.pk).first()
        if current is None or current.status != ManualControlSession.ACTIVE or not current.start_key_hash:
            return None
        if not hmac.compare_digest(current.start_key_hash, _hash(key)):
            return None
        cookie = secrets.token_urlsafe(32)
        current.start_key_hash = ""
        current.cookie_hash = _hash(cookie)
        now = _now()
        current.last_seen_at = now
        current.last_request_at = now
        current.expires_at = now + _lease()
        current.save(update_fields=["start_key_hash", "cookie_hash", "last_seen_at", "last_request_at", "expires_at", "updated_at"])
    return cookie


def cookie_ok(session: ManualControlSession, value: str) -> bool:
    return bool(value and session.cookie_hash) and hmac.compare_digest(session.cookie_hash, _hash(value))


def touch(session: ManualControlSession, *, game_request: bool = False, closing: bool = False) -> None:
    """The operator's tab gave a sign of life (or said it is leaving)."""
    if session.status != ManualControlSession.ACTIVE:
        return
    now = _now()
    lease = _lease()
    fields: dict = {}
    if closing:
        fields["expires_at"] = now + CLOSING_GRACE
    else:
        # written at most every few seconds: a page of the game is dozens of requests
        fresh = session.expires_at and (session.expires_at - now) > (lease - timedelta(seconds=5))
        if not fresh:
            fields["expires_at"] = now + lease
            fields["last_seen_at"] = now
        if game_request and (not session.last_request_at or (now - session.last_request_at) > timedelta(seconds=5)):
            fields["last_request_at"] = now
    if not fields:
        return
    ManualControlSession.objects.filter(pk=session.pk, status=ManualControlSession.ACTIVE).update(**fields)
    for name, value in fields.items():
        setattr(session, name, value)


def count_request(session: ManualControlSession) -> None:
    from django.db.models import F

    ManualControlSession.objects.filter(pk=session.pk).update(request_count=F("request_count") + 1)


# -- giving the account back -------------------------------------------------------

def _is_expired(session: ManualControlSession, now=None) -> bool:
    now = now or _now()
    if session.status == ManualControlSession.RESUMING:
        return (now - session.updated_at) > RESUMING_TIMEOUT
    if session.status not in (ManualControlSession.PAUSING, ManualControlSession.ACTIVE):
        return False
    if session.expires_at <= now:
        return True
    idle = _idle_limit()
    if session.status == ManualControlSession.ACTIVE and idle is not None:
        last = session.last_request_at or session.activated_at or session.created_at
        return (now - last) > idle
    return False


def _expiry_reason(session: ManualControlSession, now) -> str:
    if session.status == ManualControlSession.RESUMING:
        return session.end_reason or "expired"
    if session.expires_at <= now:
        return "expired"
    return "idle"


def sweep_expired(*, game_account_id=None) -> int:
    """End every open session whose lease ran out. Returns how many were ended."""
    sessions = ManualControlSession.objects.filter(status__in=ManualControlSession.OPEN_STATUSES)
    if game_account_id:
        sessions = sessions.filter(game_account_id=game_account_id)
    now = _now()
    ended = 0
    for session in sessions:
        if _is_expired(session, now):
            end(session, _expiry_reason(session, now))
            ended += 1
    # jobs that arrived while a session was closing, or that could not be handed back then
    leftovers = HeldJob.objects.filter(session__status=ManualControlSession.ENDED)
    if game_account_id:
        leftovers = leftovers.filter(session__game_account_id=game_account_id)
    for session_id in set(leftovers.values_list("session_id", flat=True)):
        _release_held_jobs(ManualControlSession.objects.get(pk=session_id))
    return ended


def end(session: ManualControlSession, reason: str, *, error: str = "") -> bool:
    """Close the session and give the account back to the automation. False if already closed."""
    with transaction.atomic():
        current = ManualControlSession.objects.select_for_update().filter(pk=session.pk).first()
        if current is None or current.status == ManualControlSession.ENDED:
            return False
        current.status = ManualControlSession.RESUMING
        current.end_reason = current.end_reason or str(reason or "")[:32]
        current.error = current.error or str(error or "")
        current.save(update_fields=["status", "end_reason", "error", "updated_at"])

    try:
        # the agent saves the cookies back for the automation and forgets the session
        rpc.send(current.node_id, _agent_payload(current, "close"))
    except Exception:  # noqa: BLE001 - the agent also drops idle sessions by itself
        logger.warning("Could not tell the agent to close manual session %s", current.pk, exc_info=True)

    released = _release_held_jobs(current)

    now = _now()
    ManualControlSession.objects.filter(pk=current.pk).update(
        status=ManualControlSession.ENDED, open_key=None, ended_at=now,
        start_key_hash="", cookie_hash="", open_request_id="", updated_at=now,
    )
    logger.info(
        "Manual control ended: session=%s game_account=%s reason=%s jobs_resumed=%s",
        current.pk, current.game_account_id, current.end_reason, released,
    )
    return True


def _release_held_jobs(session: ManualControlSession) -> int:
    from apps.jobs.services.dispatch import dispatch_job

    held = list(
        HeldJob.objects.filter(session=session).select_related("job").order_by("job__scheduled_for", "created_at")
    )
    now = _now()
    released = 0
    for item in held:
        job = item.job
        if job.status in ("queued", "scheduled"):
            eta = now + timedelta(seconds=RESUME_FIRST_DELAY + released * RESUME_SPACING)
            try:
                dispatch_job(job, eta=eta)
            except Exception:  # noqa: BLE001 - the row stays: the next sweep tries again
                logger.warning("Could not hand job %s back to the agent", job.pk, exc_info=True)
                continue
            fields = {"updated_at": now}
            if job.status == "scheduled":
                fields["scheduled_for"] = eta
            Job.objects.filter(pk=job.pk).update(**fields)
            released += 1
        item.delete()
    return released
