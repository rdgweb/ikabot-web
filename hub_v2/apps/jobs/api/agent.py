"""Agent API views: status updates, logs, reschedule."""

import json
import logging
from datetime import timedelta
from uuid import UUID

from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.jobs.models import Job, JobLog
from core.auth.backends import AgentTokenAuthentication
from core.auth.permissions import IsAgent
from apps.settings_app.utils import get_int_setting

from .serializers import (
    JobLogEntrySerializer,
    JobRescheduleResponseSerializer,
    JobRescheduleSerializer,
    JobSpawnResponseSerializer,
    JobSpawnSerializer,
    JobStatusResponseSerializer,
    JobStatusUpdateSerializer,
)

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"finished", "error", "cancelled"}


class JobStatusView(APIView):
    """POST /api/agent/jobs/<uuid:job_id>/status/."""

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]
    serializer_class = JobStatusUpdateSerializer

    @extend_schema(
        request=JobStatusUpdateSerializer,
        responses={200: JobStatusResponseSerializer},
    )
    def post(self, request, job_id):
        serializer = JobStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            job = Job.objects.get(pk=job_id)
        except Job.DoesNotExist:
            return Response(
                {"error": "Job not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if job.status in _TERMINAL_STATUSES:
            return Response(
                {"error": f"Job is already in terminal status '{job.status}'."},
                status=status.HTTP_409_CONFLICT,
            )

        update_fields = ["status", "updated_at"]
        job.status = data["status"]

        if data.get("exit_code") is not None:
            job.exit_code = data["exit_code"]
            update_fields.append("exit_code")

        if data.get("agent"):
            job.agent = data["agent"]
            update_fields.append("agent")

        if data["status"] == "running" and job.started_at is None:
            job.started_at = timezone.now()
            update_fields.append("started_at")

        if data["status"] == "running":
            now = timezone.now()
            job.last_heartbeat_at = now
            job.lease_expires_at = now + timedelta(seconds=max(30, get_int_setting("running_job_lease_seconds", 180)))
            update_fields.extend(["last_heartbeat_at", "lease_expires_at"])
            if "progress" in data:
                job.progress_json = json.dumps(data.get("progress") or {})
                update_fields.append("progress_json")

        if data["status"] in _TERMINAL_STATUSES:
            job.finished_at = timezone.now()
            job.lease_expires_at = None
            update_fields.extend(["finished_at", "lease_expires_at"])

        job.save(update_fields=update_fields)

        logger.info(
            "Job %s status updated to '%s' (exit_code=%s)",
            job_id,
            job.status,
            job.exit_code,
        )

        return Response(
            JobStatusResponseSerializer(
                {"ok": True, "job_id": str(job.pk), "status": job.status}
            ).data
        )


class JobLogView(APIView):
    """POST /api/agent/jobs/<uuid:job_id>/logs/."""

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]
    serializer_class = JobLogEntrySerializer

    @extend_schema(
        request=JobLogEntrySerializer(many=True),
        responses={
            200: inline_serializer(
                name="JobLogResponse",
                fields={
                    "ok": drf_serializers.BooleanField(),
                    "count": drf_serializers.IntegerField(),
                },
            )
        },
        description=(
            "Recebe uma lista de entradas de log. "
            "A view também aceita um único objeto no runtime, mas a spec "
            "publica o formato em lote para manter o contrato explícito."
        ),
    )
    def post(self, request, job_id):
        try:
            job = Job.objects.get(pk=job_id)
        except Job.DoesNotExist:
            return Response(
                {"error": "Job not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        raw = request.data
        if isinstance(raw, dict):
            raw = [raw]

        if not isinstance(raw, list) or len(raw) == 0:
            return Response(
                {"error": "Expected a log entry or list of log entries."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = JobLogEntrySerializer(data=raw, many=True)
        serializer.is_valid(raise_exception=True)

        logs = [
            JobLog(job=job, level=entry["level"], message=entry.get("message", ""))
            for entry in serializer.validated_data
        ]
        JobLog.objects.bulk_create(logs)

        logger.debug("Job %s: %d log entries created", job_id, len(logs))
        return Response({"ok": True, "count": len(logs)})


class RescheduleJobView(APIView):
    """POST /api/agent/jobs/<uuid:job_id>/reschedule/."""

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]
    serializer_class = JobRescheduleSerializer

    @extend_schema(
        request=JobRescheduleSerializer,
        responses={201: JobRescheduleResponseSerializer},
    )
    def post(self, request, job_id):
        serializer = JobRescheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            job = Job.objects.get(pk=job_id)
        except Job.DoesNotExist:
            return Response(
                {"error": "Job not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        delay = serializer.validated_data["delay_seconds"]
        scheduled_for = timezone.now() + timedelta(seconds=delay)
        patch = serializer.validated_data.get("inputs")

        if patch is None:
            new_inputs_json = job.inputs_json
        else:
            try:
                existing = json.loads(job.inputs_json or "{}")
            except (json.JSONDecodeError, TypeError):
                existing = {}
            existing.update(patch)
            new_inputs_json = json.dumps(existing)

        new_job = Job.objects.create(
            account=job.account,
            game_account=job.game_account,
            node=job.node,
            profile=job.profile,
            action_code=job.action_code,
            source_job_id=job.pk,
            inputs_json=new_inputs_json,
            timeout_sec=job.timeout_sec,
            status="scheduled",
            scheduled_for=scheduled_for,
        )

        logger.info(
            "Job %s rescheduled as %s (delay=%ds, at=%s)",
            job_id,
            new_job.pk,
            delay,
            scheduled_for.isoformat(),
        )

        return Response(
            JobRescheduleResponseSerializer(
                {
                    "ok": True,
                    "new_job_id": str(new_job.pk),
                    "scheduled_for": scheduled_for,
                }
            ).data,
            status=status.HTTP_201_CREATED,
        )


class SpawnJobView(APIView):
    """POST /api/agent/jobs/<uuid:job_id>/spawn/."""

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]
    serializer_class = JobSpawnSerializer

    @extend_schema(
        request=JobSpawnSerializer,
        responses={201: JobSpawnResponseSerializer},
    )
    def post(self, request, job_id):
        serializer = JobSpawnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            parent_job = Job.objects.get(pk=job_id)
        except Job.DoesNotExist:
            return Response({"error": "Job not found."}, status=status.HTTP_404_NOT_FOUND)

        delay = serializer.validated_data["delay_seconds"]
        scheduled_for = timezone.now() + timedelta(seconds=delay) if delay > 0 else None
        new_job = Job.objects.create(
            account=parent_job.account,
            game_account=parent_job.game_account,
            node=parent_job.node,
            profile=parent_job.profile,
            action_code=serializer.validated_data["action_code"],
            source_job_id=parent_job.pk,
            inputs_json=json.dumps(serializer.validated_data.get("inputs") or {}),
            timeout_sec=serializer.validated_data.get("timeout_sec") or parent_job.timeout_sec,
            status="scheduled" if delay > 0 else "queued",
            scheduled_for=scheduled_for,
        )

        logger.info(
            "Spawned child job %s from %s (action=%s delay=%ss)",
            new_job.pk,
            parent_job.pk,
            new_job.action_code,
            delay,
        )
        return Response(
            JobSpawnResponseSerializer(
                {
                    "ok": True,
                    "new_job_id": str(new_job.pk),
                    "scheduled_for": scheduled_for,
                }
            ).data,
            status=status.HTTP_201_CREATED,
        )


class ConstructionSupportView(APIView):
    """GET /api/agent/jobs/<uuid:job_id>/construction-support/."""

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    @extend_schema(
        responses={
            200: inline_serializer(
                name="ConstructionSupportResponse",
                fields={
                    "ok": drf_serializers.BooleanField(),
                    "lineage": drf_serializers.ListField(child=drf_serializers.UUIDField()),
                    "entries": drf_serializers.ListField(child=drf_serializers.DictField()),
                },
            )
        }
    )
    def get(self, request, job_id):
        try:
            root_job = Job.objects.get(pk=job_id)
        except Job.DoesNotExist:
            return Response({"error": "Job not found."}, status=status.HTTP_404_NOT_FOUND)

        lineage: list[UUID] = []
        cursor = root_job
        seen: set[UUID] = set()
        while cursor and cursor.pk not in seen:
            seen.add(cursor.pk)
            lineage.append(cursor.pk)
            if not cursor.source_job_id:
                break
            cursor = Job.objects.filter(pk=cursor.source_job_id).first()

        descendants: list[Job] = []
        frontier = {jid for jid in lineage}
        visited = set(frontier)
        while frontier:
            children = list(
                Job.objects.filter(source_job_id__in=frontier)
                .only("id", "source_job_id", "action_code", "status", "inputs_json", "created_at")
            )
            frontier = set()
            for child in children:
                if child.pk in visited:
                    continue
                visited.add(child.pk)
                descendants.append(child)
                frontier.add(child.pk)

        entries: list[dict] = []
        for child in descendants:
            if child.action_code != 2:
                continue
            try:
                inputs = child.inputs_json if isinstance(child.inputs_json, dict) else json.loads(child.inputs_json or "{}")
            except Exception:
                inputs = {}
            monitor_mode = str(inputs.get("monitor_mode") or "").strip()
            if monitor_mode and monitor_mode != "arrival_check":
                continue

            resources = {
                "wood": 0,
                "wine": 0,
                "marble": 0,
                "crystal": 0,
                "sulfur": 0,
            }
            if monitor_mode == "arrival_check":
                sent = inputs.get("sent_resources") if isinstance(inputs.get("sent_resources"), dict) else {}
                resources["wood"] = int(sent.get("wood") or 0)
                resources["wine"] = int(sent.get("wine") or 0)
                resources["marble"] = int(sent.get("marble") or 0)
                resources["crystal"] = int(sent.get("crystal") or sent.get("glas") or 0)
                resources["sulfur"] = int(sent.get("sulfur") or 0)
            else:
                resources["wood"] = int(inputs.get("wood") or 0)
                resources["wine"] = int(inputs.get("wine") or 0)
                resources["marble"] = int(inputs.get("marble") or 0)
                resources["crystal"] = int(inputs.get("crystal") or 0)
                resources["sulfur"] = int(inputs.get("sulfur") or 0)

            if not any(resources.values()):
                continue
            if child.status not in {"queued", "running", "scheduled", "finished"}:
                continue

            entries.append(
                {
                    "job_id": str(child.pk),
                    "source_job_id": str(child.source_job_id) if child.source_job_id else "",
                    "status": child.status,
                    "monitor_mode": monitor_mode,
                    "from_city": str(inputs.get("from_city") or ""),
                    "to_city": str(inputs.get("to_city") or ""),
                    "from_city_name": str(inputs.get("from_city_name") or ""),
                    "to_city_name": str(inputs.get("to_city_name") or ""),
                    "resources": resources,
                    "created_at": child.created_at.isoformat(),
                }
            )

        return Response({"ok": True, "lineage": [str(item) for item in lineage], "entries": entries})
