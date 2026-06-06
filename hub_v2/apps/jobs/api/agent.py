"""Agent API views: status updates, logs, reschedule."""

import ast
import json
import logging
from datetime import timedelta
from uuid import UUID

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import GameAccount
from apps.jobs.models import ConstructionResourceReservation, Job, JobLog
from apps.jobs.services.workflows import create_job_with_workflow, reconcile_workflow_for_job
from apps.market.services import reconcile_internal_order_for_job
from apps.telegram.services.notifications import notify
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


def _normalize_json_object(raw) -> dict:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw or "{}")
        except Exception:
            try:
                parsed = ast.literal_eval(raw or "{}")
            except Exception:
                parsed = {}
    else:
        parsed = raw
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _serialize_login_block_state(ga: GameAccount) -> dict:
    blocked_until = ga.login_blocked_until
    active = bool(blocked_until and blocked_until > timezone.now())
    return {
        "game_account_id": str(ga.pk),
        "active": active,
        "blocked_until": blocked_until.isoformat() if blocked_until else "",
        "backoff_hours": int(ga.login_block_backoff_hours or 0),
        "reason": ga.login_block_reason or "",
    }


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

        if data["status"] in _TERMINAL_STATUSES:
            note = ""
            if data["status"] == "error":
                note = f"job_id={job.pk} exit_code={job.exit_code}"
            reconcile_internal_order_for_job(job, terminal_status=data["status"], note=note)
            reconcile_workflow_for_job(job)

        return Response(
            JobStatusResponseSerializer(
                {"ok": True, "job_id": str(job.pk), "status": job.status}
            ).data
        )


class GameAccountLoginCooldownView(APIView):
    """GET/POST /api/agent/game-accounts/<uuid:game_account_id>/login-cooldown/."""

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def get(self, request, game_account_id):
        try:
            ga = GameAccount.objects.select_related("account", "account__node").get(pk=game_account_id)
        except GameAccount.DoesNotExist:
            return Response({"error": "GameAccount not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"ok": True, **_serialize_login_block_state(ga)}, status=status.HTTP_200_OK)

    def post(self, request, game_account_id):
        mode = str(request.data.get("mode") or "").strip().lower()
        reason = str(request.data.get("reason") or "").strip()
        if mode not in {"record_400", "record_proxy", "clear"}:
            return Response({"error": "invalid_mode"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            try:
                ga = GameAccount.objects.select_for_update().select_related("account", "account__node").get(pk=game_account_id)
            except GameAccount.DoesNotExist:
                return Response({"error": "GameAccount not found."}, status=status.HTTP_404_NOT_FOUND)

            if mode == "clear":
                ga.login_blocked_until = None
                ga.login_block_backoff_hours = 0
                ga.login_block_reason = ""
                ga.save(update_fields=[
                    "login_blocked_until",
                    "login_block_backoff_hours",
                    "login_block_reason",
                    "updated_at",
                ])
                return Response({"ok": True, **_serialize_login_block_state(ga)}, status=status.HTTP_200_OK)

            prev_hours = int(ga.login_block_backoff_hours or 0)
            next_hours = max(1, prev_hours + 1)
            now = timezone.now()
            ga.login_block_backoff_hours = next_hours
            ga.login_blocked_until = now + timedelta(hours=next_hours)
            ga.login_block_reason = reason[:4000]
            ga.save(update_fields=[
                "login_blocked_until",
                "login_block_backoff_hours",
                "login_block_reason",
                "updated_at",
            ])

        if prev_hours < 8 <= next_hours:
            try:
                notify(
                    event_key="job_failed",
                    game_account=ga,
                    account=ga.account,
                    node=ga.account.node,
                    title="Login temporariamente bloqueado",
                    body=(
                        f"{ga.name or ga.server_id} entrou em backoff de login por {next_hours}h.\n"
                        f"Bloqueado até: {ga.login_blocked_until:%d/%m/%Y %H:%M:%S}\n"
                        f"Motivo: {reason or ('loginLink 400' if mode == 'record_400' else 'Falha de proxy no lobby')}"
                    ),
                )
            except Exception:
                logger.warning("Failed to notify login block for game account %s", ga.pk, exc_info=True)

        return Response({"ok": True, **_serialize_login_block_state(ga)}, status=status.HTTP_200_OK)


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

        from django.db import transaction
        with transaction.atomic():
            try:
                job = Job.objects.select_for_update().get(pk=job_id)
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

            delay = serializer.validated_data["delay_seconds"]
            scheduled_for = timezone.now() + timedelta(seconds=delay)
            patch = serializer.validated_data.get("inputs")

            existing = _normalize_json_object(job.inputs_json)
            if patch is None:
                new_inputs_dict = dict(existing)
            else:
                new_inputs_dict = dict(existing)
                new_inputs_dict.update(patch)
            new_inputs_json = json.dumps(new_inputs_dict)

            # Idempotency: only reuse a recent child if it is a true reschedule equivalent.
            # This avoids confusing ac=2 monitor children (spawned with the same workflow_run)
            # with the follow-up child that should carry the remaining payload.
            existing_children = Job.objects.filter(
                source_job_id=job.pk,
                action_code=job.action_code,
                workflow_run__trigger_type="agent_reschedule",
                created_at__gte=timezone.now() - timedelta(seconds=120),
            ).order_by("-created_at")
            existing_child = None
            for candidate in existing_children:
                candidate_inputs = _normalize_json_object(candidate.inputs_json)
                if candidate_inputs == new_inputs_dict:
                    existing_child = candidate
                    break
            if existing_child:
                logger.info(
                    "Job %s already rescheduled as %s (idempotent return)",
                    job_id, existing_child.pk,
                )
                return Response(
                    JobRescheduleResponseSerializer({
                        "ok": True,
                        "new_job_id": str(existing_child.pk),
                        "scheduled_for": existing_child.scheduled_for,
                    }).data,
                    status=status.HTTP_201_CREATED,
                )

            new_job = create_job_with_workflow(
                account=job.account,
                game_account=job.game_account,
                node=job.node,
                profile=job.profile,
                action_code=job.action_code,
                inputs=new_inputs_json,
                timeout_sec=job.timeout_sec,
                source_job=job,
                status="scheduled",
                scheduled_for=scheduled_for,
                start_new_run=True,
                trigger_type="agent_reschedule",
            )

            # Se o job fonte era "scheduled" (ainda não executado), cancelá-lo para
            # evitar execução dupla. Ocorre quando um filho acorda o pai adiantado:
            # o pai já tinha uma continuação futura (fallback) que agora é supersedida.
            if job.status == "scheduled":
                Job.objects.filter(pk=job.pk).update(status="cancelled")

            # Mailbox pattern: se o reschedule carrega __campaign_root_id,
            # atualiza o ponteiro current_runner_id no job raiz para que filhos
            # sempre saibam qual é o fallback atual ao notificarem.
            if patch and isinstance(patch, dict):
                root_id_str = str(patch.get("__campaign_root_id") or "").strip()
                if root_id_str:
                    try:
                        root_progress = Job.objects.filter(pk=root_id_str).values_list("progress_json", flat=True).first()
                        if root_progress is not None:
                            prog = json.loads(root_progress) if isinstance(root_progress, str) and root_progress else (root_progress or {})
                            prog["current_runner_id"] = str(new_job.pk)
                            Job.objects.filter(pk=root_id_str).update(progress_json=json.dumps(prog))
                    except Exception as _e:
                        logger.warning("Falha ao atualizar current_runner_id no root %s: %s", root_id_str, _e)

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


class RetimeRootFollowupJobView(APIView):
    """POST /api/agent/jobs/retime-followup/."""

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request):
        root_job_id = str(request.data.get("root_job_id") or "").strip()
        exclude_job_id = str(request.data.get("exclude_job_id") or "").strip()
        try:
            action_code = int(request.data.get("action_code"))
            delay_seconds = max(0, int(request.data.get("delay_seconds") or 0))
        except (TypeError, ValueError):
            return Response({"error": "action_code and delay_seconds must be numeric"}, status=status.HTTP_400_BAD_REQUEST)

        if not root_job_id:
            return Response({"error": "root_job_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        candidate = (
            Job.objects.filter(
                root_job_id=root_job_id,
                action_code=action_code,
                status__in=("queued", "scheduled"),
            )
            .exclude(pk=exclude_job_id or None)
            .order_by("scheduled_for", "created_at")
            .first()
        )
        if candidate is None:
            return Response({"ok": False, "updated": False, "reason": "followup_not_found"}, status=status.HTTP_200_OK)

        candidate.status = "scheduled"
        candidate.scheduled_for = now + timedelta(seconds=delay_seconds)
        candidate.save(update_fields=["status", "scheduled_for", "updated_at"])
        JobLog.objects.create(
            job=candidate,
            level="info",
            message=f"Reagendado por job relacionado na mesma cadeia raiz; novo ETA em {delay_seconds}s.",
        )
        return Response(
            {
                "ok": True,
                "updated": True,
                "job_id": str(candidate.pk),
                "scheduled_for": candidate.scheduled_for.isoformat() if candidate.scheduled_for else "",
            }
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

        if parent_job.status in _TERMINAL_STATUSES:
            return Response(
                {"error": f"Job is already in terminal status '{parent_job.status}'."},
                status=status.HTTP_409_CONFLICT,
            )

        delay = serializer.validated_data["delay_seconds"]
        scheduled_for = timezone.now() + timedelta(seconds=delay) if delay > 0 else None

        # Optional game_account override — must share the same server_id as parent (security).
        ga_override_id = serializer.validated_data.get("game_account_id")
        if ga_override_id:
            try:
                child_ga = GameAccount.objects.get(pk=ga_override_id)
            except GameAccount.DoesNotExist:
                return Response(
                    {"error": f"game_account_id {ga_override_id} not found."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if child_ga.server_id != parent_job.game_account.server_id:
                return Response(
                    {"error": "game_account_id must share the same server_id as the parent job."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            child_ga = parent_job.game_account

        # Explicit node_id override takes priority.
        # When game_account_id is overridden (no explicit node_id), auto-route to the
        # node that owns the child account — the calling agent doesn't know cross-node IDs.
        # When neither is overridden, inherit parent node (unchanged behavior).
        node_override_id = serializer.validated_data.get("node_id")
        if node_override_id:
            from apps.accounts.models import Node
            try:
                child_node = Node.objects.get(pk=node_override_id)
            except Node.DoesNotExist:
                return Response(
                    {"error": f"node_id {node_override_id} not found."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        elif ga_override_id:
            child_node = child_ga.account.node or parent_job.node
        else:
            child_node = parent_job.node

        new_job = create_job_with_workflow(
            account=child_ga.account,
            game_account=child_ga,
            node=child_node,
            profile=parent_job.profile,
            action_code=serializer.validated_data["action_code"],
            inputs=serializer.validated_data.get("inputs") or {},
            timeout_sec=serializer.validated_data.get("timeout_sec") or parent_job.timeout_sec,
            source_job=parent_job,
            status="scheduled" if delay > 0 else "queued",
            scheduled_for=scheduled_for,
            start_new_run=False,
            trigger_type="agent_spawn",
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


class JobStatusReadView(APIView):
    """GET /api/agent/jobs/<uuid:job_id>/info/ — read job status without mutating it."""

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def get(self, request, job_id):
        try:
            job = Job.objects.get(pk=job_id)
        except Job.DoesNotExist:
            return Response({"error": "not_found"}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            "job_id": str(job.pk),
            "status": job.status,
            "action_code": job.action_code,
            "finished": job.status in _TERMINAL_STATUSES,
        })


class ActiveSpyTargetsView(APIView):
    """GET /api/agent/jobs/active-spy-targets/?ga_id=X
    Lista alvos com jobs ac=15 ativos (running/scheduled) pro GA.
    Usado pelo SpyRunner pra identificar grupos órfãos no safehouse.
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def get(self, request):
        ga_id = request.query_params.get("ga_id")
        if not ga_id:
            return Response({"error": "missing ga_id"}, status=status.HTTP_400_BAD_REQUEST)
        qs = Job.objects.filter(
            action_code=15,
            game_account_id=ga_id,
            status__in=("running", "scheduled"),
        )
        targets = []
        for j in qs:
            inp = _normalize_json_object(j.inputs_json)
            targets.append({
                "target_owner": inp.get("target_owner") or "",
                "target_owner_id": str(inp.get("target_owner_id") or ""),
                "target_city_name": inp.get("target_city_name") or "",
                "target_city_id": str(inp.get("target_city_id") or ""),
            })
        return Response({"targets": targets})


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

        # Resolve the tree root: use root_job_id if set, otherwise this job is the root.
        # For legacy jobs without root_job_id, fall back to the old BFS walk.
        root_id = root_job.root_job_id or root_job.pk

        if root_job.root_job_id is not None or root_job.source_job_id is None:
            # Fast path: single indexed query covering the whole tree
            descendants = list(
                Job.objects.filter(root_job_id=root_id)
                .only("id", "source_job_id", "action_code", "status", "inputs_json", "created_at")
            )
        else:
            # Legacy fallback for old jobs without root_job_id
            lineage: list[UUID] = []
            cursor = root_job
            seen: set[UUID] = set()
            while cursor and cursor.pk not in seen:
                seen.add(cursor.pk)
                lineage.append(cursor.pk)
                if not cursor.source_job_id:
                    break
                cursor = Job.objects.filter(pk=cursor.source_job_id).first()
            descendants = []
            frontier = set(lineage)
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
            if child.status not in {"queued", "running", "scheduled"}:
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

        return Response({"ok": True, "lineage": [str(root_id)], "entries": entries})


class ConstructionReservationsView(APIView):
    """GET /api/agent/game-accounts/<uuid:game_account_id>/construction-reservations/."""

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    @extend_schema(
        responses={
            200: inline_serializer(
                name="ConstructionReservationsResponse",
                fields={
                    "ok": drf_serializers.BooleanField(),
                    "reservations": drf_serializers.DictField(),
                },
            )
        }
    )
    def get(self, request, game_account_id):
        query = getattr(request, "query_params", None) or request.GET
        city_ids_raw = str(query.get("city_ids") or "").strip()
        city_ids = [part.strip() for part in city_ids_raw.split(",") if part.strip()]

        qs = ConstructionResourceReservation.objects.filter(
            game_account_id=game_account_id,
            job__action_code=1002,
            status="active",
        )
        if city_ids:
            qs = qs.filter(city_id__in=city_ids)

        aggregated = (
            qs.values("city_id", "resource")
            .annotate(total=Sum("reserved_local_amount"))
            .order_by("city_id", "resource")
        )

        reservations: dict[str, dict[str, int]] = {}
        for row in aggregated:
            city_id = str(row.get("city_id") or "").strip()
            if not city_id:
                continue
            resource = str(row.get("resource") or "").strip()
            total = int(row.get("total") or 0)
            if total <= 0:
                continue
            key = "crystal" if resource == "glas" else resource
            bucket = reservations.setdefault(
                city_id,
                {"wood": 0, "wine": 0, "marble": 0, "crystal": 0, "sulfur": 0},
            )
            if key in bucket:
                bucket[key] += total

        return Response({"ok": True, "reservations": reservations})


class ConstructionReservationSyncView(APIView):
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    @staticmethod
    def _resolve_plan_job(job_id):
        try:
            job = Job.objects.get(pk=job_id)
        except Job.DoesNotExist:
            return None

        root_id = job.root_job_id or job.pk
        if str(job.action_code) == "1002" and str(job.pk) == str(root_id):
            return job

        return (
            Job.objects.filter(pk=root_id, action_code=1002)
            .first()
            or Job.objects.filter(root_job_id=root_id, action_code=1002).order_by("created_at").first()
        )

    def post(self, request, job_id):
        plan_job = self._resolve_plan_job(job_id)
        if plan_job is None:
            return Response({"ok": True, "updated": 0, "mode": "noop"})

        mode = str(request.data.get("mode") or "refresh_remaining").strip().lower()

        if mode == "apply_arrival":
            city_id = str(request.data.get("city_id") or "").strip()
            resources = request.data.get("resources") if isinstance(request.data.get("resources"), dict) else {}
            if not city_id or not resources:
                return Response({"error": "city_id and resources are required for apply_arrival"}, status=status.HTTP_400_BAD_REQUEST)

            updated = 0
            with transaction.atomic():
                active_rows = list(
                    ConstructionResourceReservation.objects.select_for_update()
                    .filter(job=plan_job, status="active", city_id=city_id)
                    .order_by("city_name", "resource", "created_at")
                )
                for resource_key, amount in resources.items():
                    amount = max(0, int(amount or 0))
                    if amount <= 0:
                        continue
                    model_resource = "glas" if str(resource_key) == "crystal" else str(resource_key)
                    rows = [row for row in active_rows if str(row.resource) == model_resource]
                    for row in rows:
                        if amount <= 0:
                            break
                        before = int(row.shortfall_amount or 0)
                        if before <= 0:
                            continue
                        consume = min(before, amount)
                        row.shortfall_amount = before - consume
                        row.save(update_fields=["shortfall_amount", "updated_at"])
                        updated += 1
                        amount -= consume

            return Response({"ok": True, "updated": updated, "mode": mode})

        if mode != "refresh_remaining":
            return Response({"error": f"unsupported mode: {mode}"}, status=status.HTTP_400_BAD_REQUEST)

        raw_reservations = request.data.get("reservations") if isinstance(request.data.get("reservations"), dict) else {}
        normalized: dict[tuple[str, str], dict[str, int]] = {}
        for city_id, resource_map in raw_reservations.items():
            city_id = str(city_id or "").strip()
            if not city_id or not isinstance(resource_map, dict):
                continue
            for resource_key, amounts in resource_map.items():
                model_resource = "glas" if str(resource_key) == "crystal" else str(resource_key)
                if not isinstance(amounts, dict):
                    continue
                reserved_local = max(0, int(amounts.get("reserved_local", 0) or 0))
                shortfall = max(0, int(amounts.get("shortfall", 0) or 0))
                if reserved_local <= 0 and shortfall <= 0:
                    continue
                normalized[(city_id, model_resource)] = {
                    "reserved_local": reserved_local,
                    "shortfall": shortfall,
                }

        updated = 0
        created = 0
        spent = 0
        with transaction.atomic():
            active_rows = list(
                ConstructionResourceReservation.objects.select_for_update()
                .filter(job=plan_job, status="active")
                .order_by("city_name", "resource", "created_at")
            )
            grouped: dict[tuple[str, str], list[ConstructionResourceReservation]] = {}
            for row in active_rows:
                grouped.setdefault((str(row.city_id), str(row.resource)), []).append(row)

            seen_keys = set()
            for key, payload in normalized.items():
                rows = grouped.get(key, [])
                target = rows[0] if rows else None
                if target is None:
                    city_id, resource = key
                    target = ConstructionResourceReservation.objects.create(
                        job=plan_job,
                        account=plan_job.account,
                        game_account=plan_job.game_account,
                        city_id=city_id,
                        city_name=city_id,
                        resource=resource,
                        reserved_local_amount=int(payload["reserved_local"]),
                        shortfall_amount=int(payload["shortfall"]),
                        status="active",
                    )
                    created += 1
                else:
                    target.reserved_local_amount = int(payload["reserved_local"])
                    target.shortfall_amount = int(payload["shortfall"])
                    target.status = "active"
                    target.save(update_fields=["reserved_local_amount", "shortfall_amount", "status", "updated_at"])
                    updated += 1
                    for extra in rows[1:]:
                        extra.status = "spent"
                        extra.save(update_fields=["status", "updated_at"])
                        spent += 1
                seen_keys.add(key)

            for key, rows in grouped.items():
                if key in seen_keys:
                    continue
                for row in rows:
                    row.status = "spent"
                    row.save(update_fields=["status", "updated_at"])
                    spent += 1

        return Response({"ok": True, "updated": updated, "created": created, "spent": spent, "mode": mode})


class NotifyParentView(APIView):
    """POST /api/agent/jobs/<uuid:job_id>/notify/

    Mailbox pattern — filho notifica o job raiz (root) de uma campanha orquestrada.
    O root guarda em progress_json["current_runner_id"] o UUID do fallback ativo.
    Este endpoint:
      1. Lê o ponteiro current_runner_id do root (qualquer status).
      2. Atomicamente (SELECT FOR UPDATE) acrescenta child_done no inbox do fallback.
      3. Se fallback.scheduled_for > agora+30s: adianta para agora+5s e re-dispatcha.

    Resolve 100% do race condition: dois filhos simultâneos enfileiram no mesmo inbox;
    só o primeiro re-dispatcha; o fallback acorda uma vez e processa ambos.
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request, job_id):
        from apps.jobs.services.dispatch import dispatch_job

        child_done = request.data.get("child_done")
        if not isinstance(child_done, dict):
            return Response({"error": "child_done must be a dict."}, status=status.HTTP_400_BAD_REQUEST)

        # Buscar job raiz (pode estar finished — só lemos o ponteiro)
        try:
            root_job = Job.objects.get(pk=job_id)
        except Job.DoesNotExist:
            return Response({"error": "Root job not found."}, status=status.HTTP_404_NOT_FOUND)

        root_progress = root_job.progress_json or {}
        if isinstance(root_progress, str):
            try:
                root_progress = json.loads(root_progress)
            except Exception:
                root_progress = {}

        current_runner_id = str(root_progress.get("current_runner_id") or "").strip()
        if not current_runner_id:
            return Response(
                {"error": "No current_runner_id on root job. Campaign may not have started."},
                status=status.HTTP_409_CONFLICT,
            )

        now = timezone.now()

        from django.db import transaction
        with transaction.atomic():
            try:
                runner_job = Job.objects.select_for_update().get(pk=current_runner_id)
            except Job.DoesNotExist:
                return Response({"error": "current_runner job not found."}, status=status.HTTP_404_NOT_FOUND)

            if runner_job.status in _TERMINAL_STATUSES:
                return Response(
                    {"error": f"Current runner {current_runner_id[:8]} is terminal ({runner_job.status}). "
                               "Campaign may be finished or pointer stale."},
                    status=status.HTTP_409_CONFLICT,
                )

            # Acrescentar notificação na inbox do fallback atual
            prog = runner_job.progress_json or {}
            if isinstance(prog, str):
                try:
                    prog = json.loads(prog)
                except Exception:
                    prog = {}
            inbox = prog.get("inbox") or []
            if not isinstance(inbox, list):
                inbox = []
            inbox.append(child_done)
            prog["inbox"] = inbox

            # Adiantar scheduled_for se estiver dormindo longe
            woke_early = False
            if runner_job.scheduled_for and runner_job.scheduled_for > now + timedelta(seconds=30):
                runner_job.scheduled_for = now + timedelta(seconds=5)
                runner_job.progress_json = prog
                runner_job.save(update_fields=["progress_json", "scheduled_for", "updated_at"])
                woke_early = True
            else:
                runner_job.progress_json = prog
                runner_job.save(update_fields=["progress_json", "updated_at"])

        if woke_early:
            dispatch_job(runner_job, eta=runner_job.scheduled_for)
            logger.info(
                "NotifyParent: root=%s runner=%s → inbox=%d entries, dispatched early",
                str(job_id)[:8], current_runner_id[:8], len(inbox),
            )
        else:
            logger.info(
                "NotifyParent: root=%s runner=%s → inbox=%d entries (already near)",
                str(job_id)[:8], current_runner_id[:8], len(inbox),
            )

        return Response({"ok": True, "inbox_size": len(inbox), "woke_early": woke_early})
