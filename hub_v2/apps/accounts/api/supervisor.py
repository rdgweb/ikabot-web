"""Outbound-only API used by one Docker host supervisor."""

import secrets

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import AgentUpdateRequest, DockerHost
from apps.jobs.models import Job


def _authenticated_host(request):
    host_id = request.META.get("HTTP_X_IKABOT_HOST_ID", "").strip()
    token = request.META.get("HTTP_X_IKABOT_HOST_TOKEN", "").strip()
    if not host_id or not token:
        return None
    try:
        host = DockerHost.objects.get(pk=host_id, active=True)
    except (DockerHost.DoesNotExist, ValueError):
        return None
    if not secrets.compare_digest(host.enrollment_token, token):
        return None
    return host


class SupervisorHeartbeatView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        host = _authenticated_host(request)
        if not host:
            return Response({"error": "Invalid host credentials."}, status=status.HTTP_403_FORBIDDEN)
        machine_id = str(request.data.get("machine_id") or "").strip()
        if not machine_id:
            return Response({"error": "machine_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        if host.machine_id and not secrets.compare_digest(host.machine_id, machine_id):
            return Response({"error": "Docker engine identity mismatch."}, status=status.HTTP_409_CONFLICT)
        duplicate = DockerHost.objects.exclude(pk=host.pk).filter(machine_id=machine_id).exists()
        if duplicate:
            return Response({"error": "Docker engine already enrolled."}, status=status.HTTP_409_CONFLICT)
        host.machine_id = machine_id
        host.supervisor_version = str(request.data.get("version") or "")[:64]
        host.supervisor_image = str(request.data.get("image") or "")[:255]
        host.last_seen_at = timezone.now()
        host.save(update_fields=["machine_id", "supervisor_version", "supervisor_image", "last_seen_at", "updated_at"])
        return Response({
            "ok": True,
            "host_id": str(host.pk),
            "managed_node_ids": [str(value) for value in host.nodes.filter(active=True).values_list("pk", flat=True)],
        })


class SupervisorUpdateNextView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        host = _authenticated_host(request)
        if not host:
            return Response({"error": "Invalid host credentials."}, status=status.HTTP_403_FORBIDDEN)
        host.last_seen_at = timezone.now()
        host.save(update_fields=["last_seen_at", "updated_at"])
        with transaction.atomic():
            candidates = list(
                AgentUpdateRequest.objects.select_for_update()
                .select_related("node")
                .filter(node__docker_host=host, node__active=True, status="queued")
                .order_by("created_at")[:10]
            )
            update = next(
                (item for item in candidates if not Job.objects.filter(node=item.node, status__in=["queued", "running"]).exists()),
                None,
            )
            if update:
                update.status = "running"
                update.started_at = timezone.now()
                update.status_message = "Solicitacao recebida pelo supervisor do host."
                update.save(update_fields=["status", "started_at", "status_message", "updated_at"])
        if not update:
            return Response({"update": None})
        return Response({"update": {
            "id": str(update.pk),
            "node_id": str(update.node_id),
            "target_version": update.target_version,
            "target_image": update.target_image,
        }})


class SupervisorUpdateStatusView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, update_id):
        host = _authenticated_host(request)
        if not host:
            return Response({"error": "Invalid host credentials."}, status=status.HTTP_403_FORBIDDEN)
        next_status = str(request.data.get("status") or "")
        if next_status not in {"running", "succeeded", "failed"}:
            return Response({"error": "Invalid status."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            update = AgentUpdateRequest.objects.get(pk=update_id, node__docker_host=host)
        except AgentUpdateRequest.DoesNotExist:
            return Response({"error": "Update request not found."}, status=status.HTTP_404_NOT_FOUND)
        allowed = {"queued": {"running", "failed"}, "running": {"running", "succeeded", "failed"}}
        if next_status not in allowed.get(update.status, set()):
            return Response({"error": "Invalid status transition."}, status=status.HTTP_409_CONFLICT)
        update.status = next_status
        update.status_message = str(request.data.get("message") or "")[:4000]
        if next_status in {"succeeded", "failed"}:
            update.finished_at = timezone.now()
        update.save(update_fields=["status", "status_message", "finished_at", "updated_at"])
        return Response({"ok": True})
