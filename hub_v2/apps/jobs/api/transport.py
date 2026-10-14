"""Agents over HTTPS only (N-106, stage 3b-2).

A customer's agent never touches the broker: it asks the hub for its due jobs, and — only while a
manual-control session is open on its node — waits on the hub for the browser's requests. The hub
decides what each node gets; the broker stays inside the server.

    POST /api/agent/jobs/claim/      {"node_id", "slots"}  -> {"jobs": [...], "manual": bool}
    GET  /api/agent/manual/next/     ?node_id&wait=20      -> {"request": {...}|null, "active": bool}
    POST /api/agent/manual/answer/   {"node_id", "id", "answer"}
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Node
from apps.jobs.models import Job
from apps.jobs.services.dispatch import build_payload
from core.auth.backends import AgentTokenAuthentication
from core.auth.permissions import IsAgent

logger = logging.getLogger(__name__)

# A claimed job the agent did not start (it died, lost the network) is offered again after this.
CLAIM_LEASE = timedelta(seconds=90)
MAX_SLOTS = 32
MAX_WAIT_SECONDS = 25


def _node(request) -> Node | None:
    node_id = (request.data.get("node_id") if request.method == "POST" else request.query_params.get("node_id")) or \
        request.META.get("HTTP_X_AGENT_NODE_ID", "")
    try:
        return Node._base_manager.get(pk=str(node_id).strip())
    except Exception:  # noqa: BLE001 - unknown or malformed id
        return None


def manual_active(node: Node) -> bool:
    """Is there anything of manual control for this node right now?"""
    from apps.manual_control import rpc
    from apps.manual_control.models import ManualControlSession

    if ManualControlSession._base_manager.filter(node=node, status__in=ManualControlSession.OPEN_STATUSES).exists():
        return True
    try:
        return rpc.pending(node.pk)
    except Exception:  # noqa: BLE001 - without the broker there is no manual control to wait for
        return False


class JobClaimView(APIView):
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request):
        from apps.manual_control.models import HeldJob

        node = _node(request)
        if node is None:
            return Response({"error": "Node not found."}, status=status.HTTP_404_NOT_FOUND)
        if node.transport != Node.TRANSPORT_HTTPS:
            return Response({"jobs": [], "transport": node.transport, "manual": False})
        try:
            slots = max(0, min(MAX_SLOTS, int(request.data.get("slots", 1))))
        except (TypeError, ValueError):
            slots = 1

        jobs = []
        if slots:
            now = timezone.now()
            with transaction.atomic():
                due = (
                    Job._base_manager.select_for_update(skip_locked=True)
                    .filter(node=node, status__in=("queued", "scheduled"))
                    .filter(Q(scheduled_for__isnull=True) | Q(scheduled_for__lte=now))
                    .filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lt=now))
                    .exclude(pk__in=HeldJob._base_manager.values("job_id"))      # waiting for a manual session to end
                    .order_by(F("scheduled_for").asc(nulls_first=True), "created_at")[:slots]
                )
                jobs = list(due)
                if jobs:
                    Job._base_manager.filter(pk__in=[job.pk for job in jobs]).update(lease_expires_at=now + CLAIM_LEASE)
        if jobs:
            logger.info("Node %s claimed %d job(s) over HTTPS", node.pk, len(jobs))
        return Response({"jobs": [build_payload(job) for job in jobs], "transport": node.transport, "manual": manual_active(node)})


class ManualNextView(APIView):
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def get(self, request):
        from apps.manual_control import rpc

        node = _node(request)
        if node is None or node.transport != Node.TRANSPORT_HTTPS:
            return Response({"request": None, "active": False})
        try:
            wait = max(1, min(MAX_WAIT_SECONDS, int(request.query_params.get("wait", 20))))
        except (TypeError, ValueError):
            wait = 20
        message = rpc.next_for(node.pk, timeout=wait)
        return Response({"request": message, "active": message is not None or manual_active(node)})


class ManualAnswerView(APIView):
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request):
        from apps.manual_control import rpc

        node = _node(request)
        request_id = str(request.data.get("id") or "")
        answer = request.data.get("answer")
        if node is None or not request_id or not isinstance(answer, dict):
            return Response({"error": "Bad request."}, status=status.HTTP_400_BAD_REQUEST)
        if not rpc.answer_from(node.pk, request_id, answer):
            return Response({"error": "Unknown request."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"ok": True})
