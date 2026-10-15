"""Platform agent add-on (N-131): the platform runs the agent of one customer's GameForge account.

A node of the customer's organization, on the platform's machine, created and removed by the platform's
supervisor. The account moves to that node while the add-on is on and back to its previous node after.
It never reaches the game from the server's own IP: the container requires a proxy, and switching on
requires the account to have proxies.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def host():
    """The platform's machine, when configured and its supervisor is alive."""
    from .models import DockerHost

    if not settings.PLATFORM_AGENT_HOST_ID:
        return None
    return DockerHost._base_manager.filter(pk=settings.PLATFORM_AGENT_HOST_ID, active=True).first()


def running_count() -> int:
    from .models import Account

    return Account._base_manager.filter(platform_agent=True).count()


def slots_left() -> int:
    return max(0, settings.PLATFORM_AGENT_LIMIT - running_count())


def has_proxies(account) -> bool:
    from apps.proxy.models import AccountProxyReservation

    return AccountProxyReservation._base_manager.filter(
        account=account, proxy_profile__active=True, proxy_profile__last_test_status=True).exists()


def why_not(account) -> str:
    """Why switching on is not possible now ('' when it is)."""
    machine = host()
    if machine is None:
        return "O agente da plataforma ainda não está disponível."
    if not machine.is_online:
        return "A máquina da plataforma está fora do ar no momento. Tente mais tarde."
    if not account.platform_agent and slots_left() < 1:
        return "As vagas de agente da plataforma estão esgotadas no momento."
    if not account.platform_proxies and not has_proxies(account):
        return "Ligue os proxies da plataforma (ou cadastre os seus) antes: o agente da plataforma nunca sai pelo IP do servidor."
    return ""


def _image() -> str:
    from apps.notes.services import get_registry_release

    release = get_registry_release("agent")
    if release.error or not release.digest.startswith("sha256:"):
        raise RuntimeError("A versão publicada do agente não tem digest verificável.")
    return f"{release.repository}@{release.digest}"


def provision(account) -> None:
    """Create the node, move the account onto it and ask the supervisor for the container."""
    from core.encryption import encrypt

    from .models import AgentProvisionRequest, Node

    machine = host()
    with transaction.atomic():
        node = Node._base_manager.create(
            name=f"plataforma-{account.pk.hex[:8]}", organization_id=account.organization_id,
            transport=Node.TRANSPORT_HTTPS, platform_managed=True, docker_host=machine,
            description=f"Agente da plataforma para {account.label}",
        )
        token = node.new_agent_token
        AgentProvisionRequest._base_manager.create(docker_host=machine, node=node, node_ref=node.pk, action="create",
                                                   token_enc=encrypt(token))
        account.previous_node = account.node if account.node_id and not account.node.platform_managed else account.previous_node
        account.node = node
        account.platform_agent_node = node
        account.platform_agent, account.platform_agent_ends_at = True, None
        account.save(update_fields=["previous_node", "node", "platform_agent_node", "platform_agent",
                                    "platform_agent_ends_at", "updated_at"])
    _point_node_to_proxy(account, node)


def _point_node_to_proxy(account, node) -> None:
    from apps.proxy.models import ProxyProfile

    proxy = ProxyProfile._base_manager.filter(
        account_reservation__account=account, active=True, last_test_status=True, assigned_node__isnull=True).first()
    if proxy is None:
        return
    proxy.assigned_node = node
    proxy.save(update_fields=["assigned_node"])
    node.proxy = proxy.proxy_url
    node.save(update_fields=["proxy"])


def end(account) -> None:
    """Remove the container, move the account back and leave the platform node inactive."""
    from apps.proxy.models import ProxyProfile

    from .models import AgentProvisionRequest

    node = account.platform_agent_node
    with transaction.atomic():
        if node is not None:
            ProxyProfile._base_manager.filter(assigned_node=node).update(assigned_node=None)
            node.active = False
            node.proxy = ""
            node.save(update_fields=["active", "proxy", "updated_at"])
            machine = node.docker_host or host()
            if machine is not None:
                AgentProvisionRequest._base_manager.create(docker_host=machine, node=node, node_ref=node.pk, action="remove")
        previous = account.previous_node
        account.node = previous if previous is not None and previous.active else None
        account.platform_agent, account.platform_agent_ends_at, account.platform_agent_billed = False, None, False
        account.platform_agent_node = None
        account.platform_agent_payment_id, account.platform_agent_payment_url = "", ""
        account.save(update_fields=["node", "platform_agent", "platform_agent_ends_at", "platform_agent_billed",
                                    "platform_agent_node", "platform_agent_payment_id", "platform_agent_payment_url", "updated_at"])


def accounts_with_agent(organization) -> int:
    """Agents the next monthly charge includes (on, or switched off before ever being charged)."""
    from django.db.models import Q

    from .models import Account

    return Account._base_manager.filter(organization=organization, platform_agent=True).filter(
        Q(platform_agent_ends_at__isnull=True) | Q(platform_agent_billed=False)).count()


def mark_billed(organization) -> int:
    from .models import Account

    return Account._base_manager.filter(organization=organization, platform_agent=True,
                                        platform_agent_billed=False).update(platform_agent_billed=True)


def end_due(organization=None, now=None, *, include_held: bool = True) -> int:
    """Switched off and over, or the plan no longer allows work: the container goes away."""
    from apps.billing.services import state_for

    from .models import Account

    now = now or timezone.now()
    agents = Account._base_manager.filter(platform_agent=True).select_related("organization", "platform_agent_node", "previous_node")
    if organization is not None:
        agents = agents.filter(organization=organization)
    held, ended = {}, 0
    for account in agents:
        if account.organization_id not in held:
            held[account.organization_id] = include_held and not state_for(account.organization, now).allows_work
        if (account.platform_agent_ends_at and account.platform_agent_ends_at <= now) or held[account.organization_id]:
            end(account)
            ended += 1
    return ended


def schedule_all_off(organization, until) -> int:
    from django.db.models import Q

    from .models import Account

    return Account._base_manager.filter(organization=organization, platform_agent=True).filter(
        Q(platform_agent_ends_at__isnull=True) | Q(platform_agent_ends_at__gt=until)).update(platform_agent_ends_at=until)


def next_request(machine):
    """The supervisor's next work order (the key in clear text leaves once and is erased)."""
    from core.encryption import decrypt

    from .models import AgentProvisionRequest

    with transaction.atomic():
        job = (AgentProvisionRequest._base_manager.select_for_update().select_related("node")
               .filter(docker_host=machine, status="queued").order_by("created_at").first())
        if job is None:
            return None
        payload = {"id": str(job.pk), "action": job.action, "node_id": str(job.node_ref)}
        if job.action == "create":
            node = job.node
            if node is None or not node.active or not job.token_enc:
                job.status, job.message, job.finished_at = "failed", "Nó removido antes de o agente nascer.", timezone.now()
                job.save(update_fields=["status", "message", "finished_at", "updated_at"])
                return None
            hub_url = (settings.SITE_URL or settings.HUB_PUBLIC_URL).rstrip("/")
            payload.update({
                "container_name": f"ikahub-plat-{node.pk.hex[:12]}",
                "image": _image(),
                "memory_bytes": settings.PLATFORM_AGENT_MEMORY_MB * 1024 * 1024,
                "nano_cpus": 500_000_000,
                "environment": {
                    "HUB_URL": hub_url, "AGENT_NODE_ID": str(node.pk), "AGENT_TOKEN": decrypt(job.token_enc),
                    "AGENT_TRANSPORT": "https", "AGENT_NAME": node.name, "AGENT_REQUIRE_PROXY": "1",
                },
            })
            job.token_enc = ""                              # handed over once; the hub keeps only the hash
        job.status = "running"
        job.save(update_fields=["status", "token_enc", "updated_at"])
    return payload


def finish_request(machine, request_id, status: str, message: str) -> bool:
    from .models import AgentProvisionRequest

    job = AgentProvisionRequest._base_manager.filter(pk=request_id, docker_host=machine, status="running").first()
    if job is None or status not in ("succeeded", "failed"):
        return False
    job.status, job.message, job.finished_at = status, (message or "")[:2000], timezone.now()
    job.save(update_fields=["status", "message", "finished_at", "updated_at"])
    if status == "failed":
        logger.warning("Platform agent %s for node %s failed: %s", job.action, job.node_ref, job.message[:200])
    return True
