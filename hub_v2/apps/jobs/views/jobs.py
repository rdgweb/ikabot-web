"""
Job list, detail, cancel, and bulk-delete views.
"""

import json
import re
from collections import Counter
from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, F, Max, Window
from django.db.models.functions import RowNumber
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.db import models, transaction
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView

from core.actions.constants import CATEGORY_META
from core.catalogs import get_building_info
from core.contracts import ACTION_CATALOG, RESOURCE_CHOICES
from core.mixins.views import FilterSortListView
from ..filters import JobFilter, WorkflowFilter
from ..models import ConstructionResourceReservation, Job, JobLog, Workflow, WorkflowRun
from ..services.dispatch import dispatch_job
from ..services.workflows import create_job_with_workflow, ensure_workflow_for_job


RESOURCE_ICON_MAP = {
    "wood": ("Madeira", "game/resources/icon_wood.png"),
    "wine": ("Vinho", "game/resources/icon_wine.png"),
    "marble": ("Marmore", "game/resources/icon_marble.png"),
    "crystal": ("Cristal", "game/resources/icon_glass.png"),
    "sulfur": ("Enxofre", "game/resources/icon_sulfur.png"),
}

DONATION_TYPE_META = {
    "wood": {"label": "Floresta", "icon": "game/resources/icon_wood.png"},
    "resource": {"label": "Floresta", "icon": "game/resources/icon_wood.png"},
    "tradegood": {"label": "Bem comercial", "icon": "game/resources/icon_wine.png"},
}

DONATION_METHOD_META = {
    "1": "Excedente do armazem",
    "2": "% da producao no intervalo",
    "3": "Quantidade fixa",
}
DONATION_POST_PRODUCTION_META = {
    "preserve": "Manter % atual",
    "custom": "Usar % customizada",
}
SHRINE_GOD_META = {
    "god_pan": {"id": 1, "key": "pan", "label": "Pan", "subtitle": "Madeira", "icon": "game/gods/pan.png", "effect_icon": "game/resources/icon_wood.png"},
    "god_dionysus": {"id": 2, "key": "dionysus", "label": "Dionisio", "subtitle": "Vinho", "icon": "game/gods/dionysus.png", "effect_icon": "game/resources/icon_wine.png"},
    "god_tyche": {"id": 3, "key": "tyche", "label": "Tique", "subtitle": "Marmore", "icon": "game/gods/tyche.png", "effect_icon": "game/resources/icon_marble.png"},
    "god_plutus": {"id": 4, "key": "plutus", "label": "Pluto", "subtitle": "Ouro", "icon": "game/gods/plutus.png", "effect_icon": "game/gods/favor.png"},
    "god_theia": {"id": 5, "key": "theia", "label": "Teia", "subtitle": "Cristal", "icon": "game/gods/theia.png", "effect_icon": "game/resources/icon_glass.png"},
    "god_hephaestus": {"id": 6, "key": "hephaestus", "label": "Hefesto", "subtitle": "Enxofre", "icon": "game/gods/hephaestus.png", "effect_icon": "game/resources/icon_sulfur.png"},
}

CONSTRUCTION_QUEUE_STRATEGY_META = {
    "fifo": "Ordem do plano",
    "eta_first": "Menor ETA primeiro",
    "smart": "Balanceado (tempo + recursos)",
}
RESEARCH_BRANCH_META = {
    "seafaring": {"label": "Navegacao Maritima", "subtitle": "Navios, porto e mar", "icon": "bi-water", "resource_icon": "game/resources/icon_wood.png"},
    "economy": {"label": "Economia", "subtitle": "Crescimento e recursos", "icon": "bi-coin", "resource_icon": "game/resources/icon_population.png"},
    "knowledge": {"label": "Ciencia", "subtitle": "Academias e pesquisa", "icon": "bi-lightbulb", "resource_icon": "game/resources/icon_glass.png"},
    "military": {"label": "Militar", "subtitle": "Exercito e defesa", "icon": "bi-shield-check", "resource_icon": "game/resources/icon_sulfur.png"},
    "mythology": {"label": "Mitologia", "subtitle": "Deuses e milagres", "icon": "bi-stars", "resource_icon": "game/gods/favor.png"},
}
SCIENTIST_TARGET_MODE_META = {
    "absolute": "Quantidade fixa",
    "percent_max": "% do maximo",
}


def _cancel_active_construction_reservations_for_jobs(job_qs) -> int:
    job_ids = list(job_qs.values_list("pk", flat=True))
    if not job_ids:
        return 0
    return ConstructionResourceReservation.objects.filter(
        job_id__in=job_ids,
        status="active",
    ).update(status="cancelled")


def _chain_jobs_qs(job: Job):
    root_id = job.root_job_id or job.pk
    return Job.objects.filter(
        models.Q(pk=root_id) | models.Q(root_job_id=root_id)
    )


_WORKFLOW_BORDER_COLOR = {
    "active": "var(--ik-sea)",
    "waiting": "#f59e0b",
    "problem": "var(--ik-bad)",
    "paused": "var(--ik-muted)",
    "finished": "var(--ik-good)",
    "cancelled": "var(--ik-line)",
    "draft": "var(--ik-line)",
}

_WORKFLOW_CANCELABLE = {"draft", "active", "paused", "waiting", "problem"}
_WORKFLOW_DELETABLE = {"finished", "cancelled"}


def _to_int(raw, default=0):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _duration_human(seconds):
    if seconds in (None, ""):
        return ""
    seconds = max(0, _to_int(seconds, 0))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        minutes, rem = divmod(seconds, 60)
        return f"{minutes}min" if rem == 0 else f"{minutes}min {rem}s"
    if seconds < 86400:
        hours, rem = divmod(seconds, 3600)
        minutes = rem // 60
        return f"{hours}h" if minutes == 0 else f"{hours}h {minutes}min"
    days, rem = divmod(seconds, 86400)
    hours = rem // 3600
    return f"{days}d" if hours == 0 else f"{days}d {hours}h"


def _parse_json_object(raw, default=None):
    fallback = {} if default is None else default
    if isinstance(raw, dict):
        return raw
    if not raw:
        return fallback
    try:
        data = json.loads(raw)
    except Exception:
        return fallback
    return data if isinstance(data, dict) else fallback


def _build_log_rows(job: Job, logs) -> list[dict]:
    rows = []
    previous_at = None
    started_at = job.started_at or job.created_at
    for log in logs:
        created_at = log.created_at
        delta_prev = ""
        elapsed_total = ""
        if previous_at is not None:
            delta_prev = _duration_human(int(max(0.0, (created_at - previous_at).total_seconds())))
        if started_at is not None:
            elapsed_total = _duration_human(int(max(0.0, (created_at - started_at).total_seconds())))
        rows.append(
            {
                "log": log,
                "delta_prev": delta_prev,
                "elapsed_total": elapsed_total,
            }
        )
        previous_at = created_at
    return rows


def _action_meta(action_code: int) -> dict:
    return ACTION_CATALOG.get(int(action_code), {})


def _can_execute_now(job: Job) -> bool:
    if job.status != "scheduled":
        return False
    return bool(_action_meta(int(job.action_code)).get("allow_execute_now", True))


def _can_retry(job: Job) -> bool:
    if job.status != "error":
        return False
    return bool(_action_meta(int(job.action_code)).get("allow_retry", True))


class JobListView(FilterSortListView):
    model = Job
    filterset_class = JobFilter
    template_name = "jobs/job_list.html"
    partial_template_name = "jobs/partials/job_table.html"
    paginate_by = 25
    ordering_fields = ["status", "action_code", "created_at", "started_at"]
    default_ordering = "-created_at"
    # Show only root jobs (root_job_id IS NULL) — child/rescheduled jobs are
    # hidden from the list and grouped under their root. Legacy jobs without
    # root_job_id are shown as-is (they predate the field).
    queryset = Job.objects.select_related("account", "game_account", "node").filter(
        root_job_id__isnull=True
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page_jobs = list(context.get("page_obj").object_list if context.get("page_obj") else context.get("object_list", []))
        active_descendants = self._load_active_descendants(page_jobs)
        chain_history_counts = self._load_chain_history_counts(page_jobs)
        recent_log_messages = self._load_recent_log_messages(page_jobs)
        grouped_rows = self._build_grouped_rows(page_jobs, active_descendants, chain_history_counts, recent_log_messages)
        context["grouped_rows"] = grouped_rows
        action_options = []
        seen_actions = set()
        full_jobs = context.get("object_list", [])
        for job in full_jobs:
            code = int(job.action_code)
            if code in seen_actions:
                continue
            seen_actions.add(code)
            action_options.append({
                "code": code,
                "label": self._action_name(code),
            })
        action_options.sort(key=lambda item: item["label"].lower())
        context["action_filter_options"] = action_options
        context["category_filter_options"] = self._category_filter_options(full_jobs)
        context["workflow_summary"] = self._workflow_summary(grouped_rows)
        context["job_view_mode"] = self.request.GET.get("view", "ops").strip().lower() or "ops"
        return context

    @staticmethod
    def _load_active_descendants(jobs):
        """For terminal root jobs on this page, fetch the latest active descendant.

        One extra query per page — returns {root_pk: latest_active_child}.
        """
        _TERMINAL = {"finished", "error", "cancelled"}
        terminal_pks = [j.pk for j in jobs if j.status in _TERMINAL]
        if not terminal_pks:
            return {}
        result = {}
        qs = (
            Job.objects.filter(
                root_job_id__in=terminal_pks,
                status__in=["queued", "running", "scheduled"],
            )
            .select_related("account", "game_account", "node")
            .order_by("root_job_id", "-created_at")
        )
        for child in qs:
            if child.root_job_id not in result:
                result[child.root_job_id] = child
        return result

    @staticmethod
    def _load_chain_history_counts(jobs):
        """Load only the number of child jobs for each root job on the page.

        Returns {root_pk: child_count}.
        The full child rows are fetched on demand via HTMX.
        """
        root_pks = [j.pk for j in jobs]
        if not root_pks:
            return {}
        rows = (
            Job.objects.filter(root_job_id__in=root_pks)
            .values("root_job_id")
            .annotate(total=Count("id"))
        )
        return {row["root_job_id"]: int(row["total"]) for row in rows}

    @staticmethod
    def _load_recent_log_messages(jobs, *, per_job=6):
        job_ids = [job.pk for job in jobs]
        if not job_ids:
            return {}
        rows = (
            JobLog.objects.filter(job_id__in=job_ids)
            .annotate(
                row_number=Window(
                    expression=RowNumber(),
                    partition_by=[F("job_id")],
                    order_by=F("created_at").desc(),
                )
            )
            .filter(row_number__lte=per_job)
            .order_by("job_id", "-created_at")
            .values_list("job_id", "message")
        )
        messages = {}
        for job_id, message in rows:
            messages.setdefault(job_id, []).append(message)
        return messages

    def _build_grouped_rows(self, jobs, active_descendants=None, chain_history_counts=None, recent_log_messages=None):
        parent_map = self._load_parent_map(jobs)
        active_descendants = active_descendants or {}
        chain_history_counts = chain_history_counts or {}
        recent_log_messages = recent_log_messages or {}
        grouped = {}
        ordered_keys = []

        for job in jobs:
            _jinputs = self._parse_inputs(job)
            action_name = self._action_name(job.action_code, _jinputs)
            city_label = self._city_label(job)
            resource_items = self._resource_items(job)
            root_job = self._resolve_root_job(job, parent_map)
            group_key = self._group_key(job, root_job)

            if group_key not in grouped:
                grouped[group_key] = {
                    "key": f"group-{len(ordered_keys)}",
                    "action_name": action_name,
                    "account_label": job.account.label,
                    "subaccount_label": self._subaccount_label(job),
                    "node_name": job.node.name,
                    "group_type": self._group_type(job),
                    "root_job": root_job,
                    "created_at": job.created_at,
                    "started_at": job.started_at,
                    "jobs": [],
                    "status_counts": Counter(),
                    "city_labels": [],
                    "resource_totals": Counter(),
                    "recent_log_messages": recent_log_messages,
                    # Latest active descendant when the root itself is terminal
                    "chain_active_job": active_descendants.get(root_job.pk),
                    # Child jobs are fetched on demand for history expand
                    "chain_history_count": int(chain_history_counts.get(root_job.pk, 0)),
                }
                ordered_keys.append(group_key)

            entry = grouped[group_key]
            entry["jobs"].append({
                "object": job,
                "city_label": city_label,
                "subaccount_label": self._subaccount_label(job),
                "resource_items": resource_items,
            })
            entry["status_counts"][job.status] += 1
            if city_label and city_label not in entry["city_labels"]:
                entry["city_labels"].append(city_label)
            for item in resource_items:
                entry["resource_totals"][item["key"]] += int(item["amount"])
            if job.created_at > entry["created_at"]:
                entry["created_at"] = job.created_at
            if job.started_at and (entry["started_at"] is None or job.started_at > entry["started_at"]):
                entry["started_at"] = job.started_at

        rows = []
        for key in ordered_keys:
            entry = grouped[key]
            city_labels = entry["city_labels"]
            entry["city_summary"] = self._city_summary(city_labels)
            entry["is_group"] = len(entry["jobs"]) > 1
            entry["total_jobs"] = len(entry["jobs"])
            entry["cycle_count"] = int(entry["chain_history_count"]) + 1
            # When root is terminal but chain is still active, reflect the active status
            chain_job = entry["chain_active_job"]
            if chain_job:
                display_counts = Counter({chain_job.status: 1})
            else:
                display_counts = entry["status_counts"]
            entry["effective_job"] = chain_job or entry["root_job"]
            entry["workflow_state"] = self._workflow_state(entry["effective_job"])
            entry["workflow_state_label"] = self._workflow_state_label(entry["workflow_state"])
            entry["status_summary"] = self._status_summary(display_counts)
            entry["status_badges"] = [(status, amount) for status, amount in display_counts.items()]
            if entry["group_type"] in {"construction", "transport"}:
                entry["resource_items"] = self._resource_items(entry["root_job"])
            else:
                entry["resource_items"] = self._resource_total_items(entry["resource_totals"])
            entry["transport_display"] = self._transport_display(
                entry["root_job"],
                child_jobs=[item["object"] for item in entry["jobs"][1:]],
            ) if entry["group_type"] == "transport" else {}
            entry["summary_lines"] = self._summary_lines(entry)
            entry["workflow_kind_label"] = self._workflow_kind_label(entry)
            entry["workflow_category"] = self._workflow_category(entry["root_job"])
            entry["workflow_category_label"] = self._workflow_category_label(entry["root_job"])
            entry["workflow_label"] = self._workflow_label(entry)
            entry["workflow_scope"] = self._workflow_scope(entry)
            entry["active_job_label"] = self._active_job_label(entry)
            entry["detail_job"] = entry["effective_job"]
            entry["progress_label"] = self._progress_label(entry)
            # Resolve scheduled_for for the column display
            if chain_job and chain_job.scheduled_for:
                entry["scheduled_for"] = chain_job.scheduled_for
            elif not entry["is_group"] and entry["jobs"][0]["object"].scheduled_for:
                entry["scheduled_for"] = entry["jobs"][0]["object"].scheduled_for
            else:
                # For groups: pick the earliest scheduled_for among scheduled jobs
                scheduled = [
                    item["object"].scheduled_for
                    for item in entry["jobs"]
                    if item["object"].status == "scheduled" and item["object"].scheduled_for
                ]
                entry["scheduled_for"] = min(scheduled) if scheduled else None
            rows.append(entry)
        return rows

    @staticmethod
    def _load_parent_map(jobs):
        # With root_job_id, we only need to fetch the root jobs themselves —
        # O(1) queries instead of N walks up the source_job_id chain.
        root_ids = {job.root_job_id for job in jobs if job.root_job_id}
        if not root_ids:
            return {}
        return Job.objects.select_related("account", "game_account", "node").in_bulk(root_ids)

    @staticmethod
    def _group_type(job):
        action_code = int(job.action_code)
        if action_code == 1002:
            return "construction"
        if action_code == 2:
            return "transport"
        return "single"

    def _resolve_root_job(self, job, parent_map):
        if job.root_job_id:
            return parent_map.get(job.root_job_id, job)
        return job

    def _group_key(self, job, root_job):
        group_type = self._group_type(job)
        if group_type in {"construction", "transport"}:
            return (group_type, str(root_job.pk))
        return (
            str(job.game_account_id or job.account_id),
            int(job.action_code),
            job.created_at.date().isoformat(),
        )

    @staticmethod
    def _action_name(action_code, inputs=None):
        action_info = ACTION_CATALOG.get(int(action_code))
        base = action_info["name"] if action_info else f"Acao #{action_code}"
        if not inputs:
            return base
        # Enrich label for donation loop actions
        _DONATION_TYPE_LABELS = {
            "wood": "Madeira",
            "tradegood": "Bem de Luxo",
            "wine": "Vinho",
            "marble": "Mármore",
            "crystal": "Cristal",
            "sulfur": "Enxofre",
        }
        if int(action_code) in {901, 902, 1006}:
            city = str(inputs.get("city_name") or inputs.get("city_id") or "").strip()
            dtype = str(inputs.get("donation_type") or "").strip()
            dtype_label = _DONATION_TYPE_LABELS.get(dtype, dtype)
            if city and dtype_label:
                return f"{base} — {city} — {dtype_label}"
            if city:
                return f"{base} — {city}"
        return base

    @staticmethod
    def _subaccount_label(job):
        if job.game_account:
            return job.game_account.name or job.game_account.server_id
        return job.account.label

    @staticmethod
    def _parse_inputs(job):
        try:
            return json.loads(job.inputs_json or "{}")
        except Exception:
            return {}

    @staticmethod
    def _bool_label(value):
        return "Sim" if bool(value) else "Nao"

    @staticmethod
    def _category_filter_options(jobs):
        options = {}
        for job in jobs:
            meta = _action_meta(int(job.action_code))
            category_key = str(meta.get("category") or "").strip()
            if not category_key:
                continue
            options[category_key] = CATEGORY_META.get(category_key, {}).get("label", category_key.title())
        return [
            {"key": key, "label": label}
            for key, label in sorted(options.items(), key=lambda item: item[1].lower())
        ]

    @staticmethod
    def _workflow_state(job):
        if job.status in {"queued", "running", "scheduled"}:
            return "active"
        if job.status == "error":
            return "problem"
        if job.status == "finished":
            return "done"
        if job.status == "cancelled":
            return "stopped"
        return "done"

    @staticmethod
    def _workflow_state_label(state):
        return {
            "active": "Ativo",
            "problem": "Com problema",
            "done": "Concluido",
            "stopped": "Cancelado",
        }.get(state, "Desconhecido")

    @staticmethod
    def _workflow_category(job):
        return str(_action_meta(int(job.action_code)).get("category") or "").strip()

    @classmethod
    def _workflow_category_label(cls, job):
        category_key = cls._workflow_category(job)
        return CATEGORY_META.get(category_key, {}).get("label", "Operacional")

    def _workflow_kind_label(self, entry):
        if entry["group_type"] == "construction":
            return "Plano"
        if entry["group_type"] == "transport":
            return "Rota"
        if entry["is_group"]:
            return "Workflow agregado"
        return "Execucao pontual"

    def _workflow_label(self, entry):
        root = entry["root_job"]
        action_name = self._action_name(root.action_code, self._parse_inputs(root))
        if entry["group_type"] in {"construction", "transport"}:
            return f"{action_name} operacional"
        if entry["is_group"]:
            return f"{action_name} agrupado"
        return action_name

    @staticmethod
    def _workflow_scope(entry):
        parts = [entry["account_label"]]
        if entry["subaccount_label"] and entry["subaccount_label"] != entry["account_label"]:
            parts.append(entry["subaccount_label"])
        if entry["city_summary"] and entry["city_summary"] != "-":
            parts.append(entry["city_summary"])
        return " / ".join(parts)

    def _active_job_label(self, entry):
        job = entry["effective_job"]
        if entry["chain_active_job"]:
            return f"Ciclo ativo #{str(job.pk)[:8]}"
        if entry["cycle_count"] > 1:
            return f"Ultimo ciclo #{str(job.pk)[:8]}"
        return f"Job #{str(job.pk)[:8]}"

    @staticmethod
    def _progress_label(entry):
        if entry["group_type"] == "construction":
            summary_lines = entry.get("summary_lines") or []
            if summary_lines:
                return summary_lines[0]
        if entry["cycle_count"] > 1:
            return f"{entry['cycle_count']} ciclo(s) registrados"
        if entry["is_group"]:
            return f"{entry['total_jobs']} job(s) agregados nesta pagina"
        return "Execucao unica"

    @staticmethod
    def _workflow_summary(rows):
        summary = {
            "total": len(rows),
            "active": 0,
            "problem": 0,
            "done": 0,
            "stopped": 0,
            "scheduled": 0,
        }
        for row in rows:
            state = row.get("workflow_state")
            if state in summary:
                summary[state] += 1
            if row.get("scheduled_for"):
                summary["scheduled"] += 1
        return summary

    @classmethod
    def _transport_mode_label(cls, inputs):
        if str(inputs.get("monitor_mode") or "").strip().lower() == "arrival_check":
            return "Monitor de chegada"
        if any(_to_int(inputs.get(key), 0) > 0 for key in ("eta_queue_seconds", "eta_loading_seconds", "eta_travel_seconds", "eta_total_seconds")):
            return "Confirmacao de chegada"
        return "Envio"

    @classmethod
    def _transport_eta_from_inputs(cls, inputs):
        eta_items = []
        mapping = (
            ("eta_queue_seconds", "Fila"),
            ("eta_loading_seconds", "Carregamento"),
            ("eta_travel_seconds", "Viagem"),
            ("eta_total_seconds", "ETA total"),
        )
        for key, label in mapping:
            raw = inputs.get(key)
            if raw in (None, "", 0, "0"):
                continue
            human = _duration_human(raw)
            if human:
                eta_items.append({"label": label, "seconds": _to_int(raw, 0), "human": human})
        return eta_items

    @classmethod
    def _extract_transport_eta_from_logs(cls, logs):
        for message in logs:
            if "ETA transporte:" not in message:
                continue
            found = {}
            for key, label in (
                ("fila", "Fila"),
                ("carregamento", "Carregamento"),
                ("viagem", "Viagem"),
                ("total", "ETA total"),
            ):
                match = re.search(rf"{key}=(\d+)s", message)
                if match:
                    found[label] = int(match.group(1))
            if found:
                return [
                    {"label": label, "seconds": seconds, "human": _duration_human(seconds)}
                    for label, seconds in found.items()
                ]
        return []

    @classmethod
    def _transport_load_label(cls, inputs):
        raw = str(inputs.get("transport_load_percent") or "").strip()
        return f"{raw}%" if raw else ""

    @classmethod
    def _transport_display(cls, job, *, child_jobs=None, logs=None):
        inputs = cls._parse_inputs(job)
        if int(job.action_code) != 2:
            return {}

        child_jobs = list(child_jobs or [])
        logs = list(logs or [])
        route = cls._city_label(job)
        eta_items = cls._transport_eta_from_inputs(inputs)
        if not eta_items:
            for child in child_jobs:
                child_inputs = cls._parse_inputs(child)
                eta_items = cls._transport_eta_from_inputs(child_inputs)
                if eta_items:
                    break
        if not eta_items and logs:
            eta_items = cls._extract_transport_eta_from_logs(logs)

        next_check_seconds = 0
        for candidate in [inputs, *(cls._parse_inputs(child) for child in child_jobs)]:
            next_check_seconds = max(next_check_seconds, _to_int(candidate.get("next_check_seconds"), 0))
            if next_check_seconds:
                break

        dispatched_total = 0
        pending_total = 0
        baseline = inputs.get("sent_resources") if isinstance(inputs.get("sent_resources"), dict) else {}
        if baseline:
            dispatched_total = sum(_to_int(baseline.get(key), 0) for key in RESOURCE_ICON_MAP)
        else:
            dispatched_total = sum(_to_int(inputs.get(key), 0) for key in RESOURCE_ICON_MAP)
        if child_jobs:
            pending_total = sum(
                max(
                    0,
                    sum(_to_int(cls._parse_inputs(child).get(key), 0) for key in RESOURCE_ICON_MAP),
                )
                for child in child_jobs
                if str(cls._parse_inputs(child).get("monitor_mode") or "").strip().lower() != "arrival_check"
                and child.status in {"queued", "scheduled", "running"}
            )

        return {
            "route": route,
            "mode_label": cls._transport_mode_label(inputs),
            "load_label": cls._transport_load_label(inputs),
            "confirm_arrival": cls._bool_label(inputs.get("confirm_arrival", True)),
            "confirmation_margin_minutes": _to_int(inputs.get("confirmation_margin_minutes"), 0),
            "eta_items": eta_items,
            "next_check_human": _duration_human(next_check_seconds) if next_check_seconds else "",
            "dispatched_total": dispatched_total,
            "pending_total": pending_total,
            "is_monitor": str(inputs.get("monitor_mode") or "").strip().lower() == "arrival_check",
        }

    @classmethod
    def _donation_display(cls, job, *, child_jobs=None, logs=None):
        inputs = cls._parse_inputs(job)
        action_code = int(job.action_code)
        if action_code not in {901, 902, 1006}:
            return {}

        child_jobs = list(child_jobs or [])
        logs = list(logs or [])

        cities = []
        if inputs.get("city_name") or inputs.get("city_id"):
            cities.append(str(inputs.get("city_name") or inputs.get("city_id")).strip())
        elif isinstance(inputs.get("cities"), list):
            cities = [str(city).strip() for city in inputs.get("cities") if str(city).strip()]

        donation_raw = inputs.get("donation_type")
        donation_values = donation_raw if isinstance(donation_raw, list) else [donation_raw] if donation_raw else []
        donation_items = []
        seen_donation = set()
        for raw in donation_values:
            key = str(raw or "").strip().lower()
            meta = DONATION_TYPE_META.get(key)
            if not meta or key in seen_donation:
                continue
            seen_donation.add(key)
            donation_items.append({"key": key, **meta})

        next_check_seconds = 0
        for candidate in [inputs, *(cls._parse_inputs(child) for child in child_jobs)]:
            next_check_seconds = max(next_check_seconds, _to_int(candidate.get("next_check_seconds"), 0))
            if next_check_seconds:
                break

        modify_job_id = ""
        for message in reversed(logs):
            match = re.search(r"Ajuste de producao agendado: job=([0-9a-f-]{8,})", message)
            if match:
                modify_job_id = match.group(1)
                break

        finish_at_ts = _to_int(inputs.get("finish_at_ts"), 0)
        finish_eta_human = _duration_human(max(0, finish_at_ts)) if finish_at_ts and finish_at_ts < 8640000000 else ""

        return {
            "is_loop": action_code in {902, 1006},
            "cities": cities,
            "city_summary": cls._city_summary(cities),
            "donation_items": donation_items,
            "method_label": DONATION_METHOD_META.get(str(inputs.get("donation_method") or "").strip(), ""),
            "method_value": _to_int(inputs.get("method_value"), 0),
            "amount": _to_int(inputs.get("amount"), 0),
            "target_level": _to_int(inputs.get("target_level"), 0),
            "post_production_mode_label": DONATION_POST_PRODUCTION_META.get(str(inputs.get("post_production_mode") or "preserve").strip(), "Manter % atual"),
            "post_sawmill_percent": _to_int(inputs.get("post_sawmill_percent"), 0),
            "post_luxury_percent": _to_int(inputs.get("post_luxury_percent"), 0),
            "carry_over_amount": _to_int(inputs.get("carry_over_amount"), 0),
            "interval_minutes": _to_int(inputs.get("interval_minutes"), 0),
            "random_wait_minutes": _to_int(inputs.get("random_wait_minutes"), 0),
            "next_check_human": _duration_human(next_check_seconds) if next_check_seconds else "",
            "finish_at_ts": finish_at_ts,
            "finish_eta_human": finish_eta_human,
            "modify_job_id": modify_job_id,
        }

    @classmethod
    def _shrine_display(cls, job, *, child_jobs=None, logs=None):
        inputs = cls._parse_inputs(job)
        action_code = int(job.action_code)
        if action_code not in {5, 1007}:
            return {}

        logs = list(logs or [])
        selected_gods = []
        for field_name, meta in SHRINE_GOD_META.items():
            if bool(inputs.get(field_name)):
                selected_gods.append({
                    "field": field_name,
                    "id": meta["id"],
                    "label": meta["label"],
                    "subtitle": meta["subtitle"],
                    "icon": meta["icon"],
                    "effect_icon": meta["effect_icon"],
                })

        city_name = ""
        building_level = 0
        favor_before = 0
        favor_after = 0
        favor_needed = 0
        additional_cities = 0
        activated_names = []
        next_check_seconds = 0
        wait_reason = ""

        for message in logs:
            if message.startswith("Santuario: "):
                match = re.search(
                    r"Santuario:\s*(?P<city>.+?)\s*\|\s*nivel=(?P<level>\d+|\?)\s*\|\s*favor=(?P<favor>\d+)\s*\|\s*deuses=(?P<gods>.+?)\s*\|\s*cidades_extra=(?P<extra>\d+)",
                    message,
                )
                if match:
                    city_name = match.group("city").strip()
                    building_level = _to_int(match.group("level"), 0)
                    favor_before = _to_int(match.group("favor"), 0)
                    additional_cities = _to_int(match.group("extra"), 0)
            elif message.startswith("Favor doado para "):
                activated_names.append(message.split("Favor doado para ", 1)[-1].strip())
            elif "favor_restante=" in message:
                match = re.search(r"favor_restante=(\d+)", message)
                if match:
                    favor_after = _to_int(match.group(1), 0)
                next_match = re.search(r"proximo_em=(\d+)s", message)
                if next_match:
                    next_check_seconds = _to_int(next_match.group(1), 0)
            elif message.startswith("Favor insuficiente: "):
                match = re.search(r"atual=(\d+)\s*\|\s*necessario=(\d+)\s*\|\s*recheck_em=(\d+)s", message)
                if match:
                    favor_before = _to_int(match.group(1), 0)
                    favor_needed = _to_int(match.group(2), 0)
                    next_check_seconds = _to_int(match.group(3), 0)
                    wait_reason = "Aguardando favor suficiente"
            elif message.startswith("Loop do santuario aguardando favor: "):
                match = re.search(r"atual=(\d+)\s*\|\s*necessario=(\d+)\s*\|\s*proximo_em=(\d+)s", message)
                if match:
                    favor_before = _to_int(match.group(1), 0)
                    favor_needed = _to_int(match.group(2), 0)
                    next_check_seconds = _to_int(match.group(3), 0)
                    wait_reason = "Loop aguardando favor"
            elif "retry_em=" in message:
                match = re.search(r"retry_em=(\d+)s", message)
                if match:
                    next_check_seconds = _to_int(match.group(1), 0)
                    wait_reason = "Retry agendado"

        selected_names = [item["label"] for item in selected_gods]
        activation_count = len(activated_names) or len(selected_names)
        if not favor_needed and activation_count:
            favor_needed = activation_count * 100

        return {
            "is_loop": action_code == 1007,
            "mode_label": "Loop" if action_code == 1007 else "Execucao unica",
            "selected_gods": selected_gods,
            "selected_summary": ", ".join(selected_names) if selected_names else "Nenhum deus selecionado",
            "activated_names": activated_names,
            "city_name": city_name,
            "building_level": building_level,
            "favor_before": favor_before,
            "favor_after": favor_after,
            "favor_needed": favor_needed,
            "additional_cities": additional_cities,
            "favor_recheck_minutes": _to_int(inputs.get("favor_recheck_minutes"), 0),
            "cycle_hours": _to_int(inputs.get("cycle_hours"), 0),
            "next_check_human": _duration_human(next_check_seconds) if next_check_seconds else "",
            "wait_reason": wait_reason,
            "favor_delta": max(0, favor_before - favor_after) if favor_before and favor_after else 0,
        }

    @classmethod
    def _miracle_display(cls, job, *, child_jobs=None, logs=None):
        inputs = cls._parse_inputs(job)
        if int(job.action_code) != 11:
            return {}

        logs = list(logs or [])
        city_name = str(inputs.get("city_name") or inputs.get("city") or "").strip()
        island_name = ""
        wonder_name = ""
        temple_level = 0
        activate_state = ""
        reason = ""
        effect = ""

        for message in logs:
            if message.startswith("Milagre: "):
                match = re.search(
                    r"Milagre:\s*(?P<city>.+?)\s*\|\s*ilha=(?P<island>.+?)\s*\|\s*templo=(?P<temple>\d+)\s*\|\s*wonder=(?P<wonder>.+?)\s*\|\s*estado=(?P<state>.+)$",
                    message,
                )
                if match:
                    city_name = match.group("city").strip()
                    island_name = match.group("island").strip()
                    temple_level = _to_int(match.group("temple"), 0)
                    wonder_name = match.group("wonder").strip()
                    activate_state = match.group("state").strip()
            elif message.startswith("Milagre indisponivel: "):
                reason = message.split("Milagre indisponivel: ", 1)[-1].strip()
            elif message.startswith("Milagre ativado: "):
                match = re.search(r"Milagre ativado:\s*(?P<wonder>.+?)\s*\|\s*efeito=(?P<effect>.+)$", message)
                if match:
                    wonder_name = match.group("wonder").strip()
                    effect = match.group("effect").strip()
            elif message.startswith("Regeneracao: "):
                reason = message.split("Regeneracao: ", 1)[-1].strip()

        return {
            "city_name": city_name,
            "island_name": island_name,
            "wonder_name": wonder_name,
            "temple_level": temple_level,
            "activate_state": activate_state,
            "reason": reason,
            "effect": effect,
        }

    @classmethod
    def _daily_login_display(cls, job, *, child_jobs=None, logs=None):
        inputs = cls._parse_inputs(job)
        if int(job.action_code) != 6:
            return {}

        logs = list(logs or [])
        city_name = str(inputs.get("city_name") or inputs.get("city") or "").strip()
        favor_before = 0
        favor_after = 0
        favor_limit = 2500
        tasks_done = 0
        tasks_count = 0
        collectible_tasks = 0
        collected_tasks = []
        fountain_state = ""
        next_check_seconds = 0

        for message in logs:
            if message.startswith("Daily tasks: "):
                match = re.search(
                    r"cidade=(?P<city>.+?)\s*\|\s*favor=(?P<favor>\d+)\/(?P<limit>\d+)\s*\|\s*tarefas=(?P<done>\d+)\/(?P<count>\d+)\s*\|\s*coletaveis=(?P<collect>\d+)",
                    message,
                )
                if match:
                    city_name = match.group("city").strip()
                    favor_before = _to_int(match.group("favor"), 0)
                    favor_limit = _to_int(match.group("limit"), 2500)
                    tasks_done = _to_int(match.group("done"), 0)
                    tasks_count = _to_int(match.group("count"), 0)
                    collectible_tasks = _to_int(match.group("collect"), 0)
            elif message.startswith("Bonus diario enviado para "):
                city_name = message.split("Bonus diario enviado para ", 1)[-1].strip() or city_name
            elif message.startswith("Favor recolhido da task "):
                match = re.search(r"Favor recolhido da task\s+(\d+):\s*(.+)$", message)
                if match:
                    collected_tasks.append({
                        "task_id": _to_int(match.group(1), 0),
                        "name": match.group(2).strip(),
                    })
            elif message == "Fonte de ambrosia coletada":
                fountain_state = "Coletada"
            elif message == "Fonte de ambrosia nao estava ativa":
                fountain_state = "Inativa"
            elif message.startswith("Login diario concluido: "):
                match = re.search(
                    r"favor=(?P<favor>\d+)\/(?P<limit>\d+)\s*\|\s*tarefas=(?P<done>\d+)\/(?P<count>\d+)\s*\|\s*coletadas=(?P<collected>\d+)\s*\|\s*proximo_em=(?P<next>\d+)s",
                    message,
                )
                if match:
                    favor_after = _to_int(match.group("favor"), 0)
                    favor_limit = _to_int(match.group("limit"), favor_limit)
                    tasks_done = _to_int(match.group("done"), tasks_done)
                    tasks_count = _to_int(match.group("count"), tasks_count)
                    next_check_seconds = _to_int(match.group("next"), 0)

        return {
            "city_name": city_name,
            "collect_favor": cls._bool_label(inputs.get("collect_favor", True)),
            "collect_fountain": cls._bool_label(inputs.get("collect_fountain", True)),
            "fallback_interval_hours": _to_int(inputs.get("fallback_interval_hours"), 24),
            "reschedule_margin_minutes": _to_int(inputs.get("reschedule_margin_minutes"), 15),
            "favor_before": favor_before,
            "favor_after": favor_after,
            "favor_limit": favor_limit,
            "tasks_done": tasks_done,
            "tasks_count": tasks_count,
            "collectible_tasks": collectible_tasks,
            "collected_tasks": collected_tasks,
            "fountain_state": fountain_state,
            "next_check_human": _duration_human(next_check_seconds) if next_check_seconds else "",
        }

    @classmethod
    def _attack_alert_display(cls, job, *, child_jobs=None, logs=None):
        inputs = cls._parse_inputs(job)
        if int(job.action_code) != 701:
            return {}

        interval_minutes = _to_int(inputs.get("interval_minutes"), 20)
        min_troops = _to_int(inputs.get("min_troops"), 0)
        min_fleet = _to_int(inputs.get("min_fleet"), 0)
        notify_telegram = cls._bool_label(inputs.get("notify_telegram", True))
        total_movements = 0
        hostile_count = 0
        matching_count = 0
        new_count = 0
        events = []
        last_error = ""

        for message in list(logs or []):
            if message.startswith("Advisor militar: "):
                match = re.search(
                    r"movimentos=(?P<total>\d+)\s*\|\s*hostis=(?P<hostile>\d+)\s*\|\s*alertaveis=(?P<matching>\d+)\s*\|\s*novos=(?P<new>\d+)",
                    message,
                )
                if match:
                    total_movements = _to_int(match.group("total"), 0)
                    hostile_count = _to_int(match.group("hostile"), 0)
                    matching_count = _to_int(match.group("matching"), 0)
                    new_count = _to_int(match.group("new"), 0)
            elif message.startswith("Ataque detectado: "):
                match = re.search(
                    r"Ataque detectado:\s*(?P<mission>.+?)\s*\|\s*(?P<origin_player>.+?)\s*\((?P<origin_city>.+?)\)\s*->\s*(?P<target_city>.+?)\s*\|\s*tropas=(?P<troops>\d+)\s*\|\s*frotas=(?P<fleet>\d+)\s*\|\s*eta=(?P<eta>.+)$",
                    message,
                )
                if match:
                    events.append({
                        "mission": match.group("mission").strip(),
                        "origin_player": match.group("origin_player").strip(),
                        "origin_city": match.group("origin_city").strip(),
                        "target_city": match.group("target_city").strip(),
                        "troops": _to_int(match.group("troops"), 0),
                        "fleet": _to_int(match.group("fleet"), 0),
                        "eta": match.group("eta").strip(),
                    })
            elif message.startswith("Falha ao varrer advisor militar: "):
                last_error = message.split("Falha ao varrer advisor militar: ", 1)[-1].strip()

        return {
            "interval_minutes": interval_minutes,
            "min_troops": min_troops,
            "min_fleet": min_fleet,
            "notify_telegram": notify_telegram,
            "total_movements": total_movements,
            "hostile_count": hostile_count,
            "matching_count": matching_count,
            "new_count": new_count,
            "events": events[:8],
            "last_error": last_error,
        }

    @classmethod
    def _research_display(cls, job, *, child_jobs=None, logs=None):
        inputs = cls._parse_inputs(job)
        if int(job.action_code) != 18:
            return {}

        logs = list(logs or [])
        selected_branches = []
        for branch_type, meta in RESEARCH_BRANCH_META.items():
            if bool(inputs.get(f"branch_{branch_type}")):
                selected_branches.append({"type": branch_type, **meta})
        city_name = ""
        branch_name = ""
        next_name = ""
        cost_text = ""
        eta_human = ""
        next_check_seconds = 0
        state_label = "Sem leitura"
        discovered_name = ""
        reason = ""
        branch_summary = ""

        for message in logs:
            if message.startswith("Pesquisa global: "):
                match = re.search(r"cidade=(?P<city>.+?)\s*\|\s*ramos=(?P<summary>.+)$", message)
                if match:
                    city_name = match.group("city").strip()
                    branch_summary = match.group("summary").strip()
                    state_label = "Monitorando"
            elif message.startswith("Pesquisa aguardando pontos: "):
                match = re.search(
                    r"ramo=(?P<branch>.+?)\s*\|\s*pesquisa=(?P<next>.+?)\s*\|\s*custo=(?P<cost>.+?)\s*\|\s*eta=(?P<eta>.+?)\s*\|\s*proximo_em=(?P<next_check>\d+)s",
                    message,
                )
                if match:
                    branch_name = match.group("branch").strip()
                    next_name = match.group("next").strip()
                    cost_text = match.group("cost").strip()
                    eta_human = match.group("eta").strip()
                    next_check_seconds = _to_int(match.group("next_check"), 0)
                    state_label = "Aguardando pontos"
            elif message.startswith("Pesquisa concluida: "):
                match = re.search(
                    r"ramo=(?P<branch>.+?)\s*\|\s*descoberta=(?P<done>.+?)\s*\|\s*proxima=(?P<next>.+)$",
                    message,
                )
                if match:
                    branch_name = match.group("branch").strip()
                    discovered_name = match.group("done").strip()
                    next_name = match.group("next").strip()
                    state_label = "Concluida"
            elif message.startswith("Pesquisa encerrada: "):
                if "todos os ramos selecionados" in message.lower():
                    state_label = "Maximo atingido"
                    reason = "Todos os ramos marcados chegaram ao máximo."
                else:
                    branch_name = re.search(r"ramo=(.+?)\s*\|", message).group(1).strip() if re.search(r"ramo=(.+?)\s*\|", message) else branch_name
                    state_label = "Maximo atingido"
                    reason = "Esse ramo nao possui mais tecnologias para descobrir."
            elif "retry_em=" in message:
                match = re.search(r"retry_em=(\d+)s", message)
                if match:
                    next_check_seconds = _to_int(match.group(1), 0)
                    state_label = "Retry agendado"
                    reason = "Houve falha operacional e o job vai tentar novamente."

        return {
            "city_name": city_name,
            "branch_name": branch_name,
            "selected_branches": selected_branches,
            "branch_summary": branch_summary,
            "next_name": next_name,
            "discovered_name": discovered_name,
            "cost_text": cost_text,
            "eta_human": eta_human,
            "fallback_interval_minutes": _to_int(inputs.get("fallback_interval_minutes"), 60),
            "ready_margin_minutes": _to_int(inputs.get("ready_margin_minutes"), 10),
            "next_check_human": _duration_human(next_check_seconds) if next_check_seconds else "",
            "state_label": state_label,
            "reason": reason,
        }

    @classmethod
    def _experiment_display(cls, job, *, child_jobs=None, logs=None):
        inputs = cls._parse_inputs(job)
        if int(job.action_code) != 26:
            return {}

        logs = list(logs or [])
        city_name = cls._city_label(job)
        crystal = 0
        crystal_cost = 0
        points_gain = 0
        crystal_after = 0
        state_label = "Sem leitura"
        reason = ""

        for message in logs:
            if message.startswith("Academia: "):
                match = re.search(
                    r"cidade=(?P<city>.+?)\s*\|\s*cristal=(?P<crystal>\d+)\s*\|\s*ensaio=(?P<points>\d+)\s*RP\s*\|\s*custo=(?P<cost>\d+)\s*\|\s*status=(?P<status>.+)$",
                    message,
                )
                if match:
                    city_name = match.group("city").strip()
                    crystal = _to_int(match.group("crystal"), 0)
                    points_gain = _to_int(match.group("points"), 0)
                    crystal_cost = _to_int(match.group("cost"), 0)
                    reason = match.group("status").strip()
                    state_label = "Painel lido"
            elif message.startswith("Ensaio concluido: "):
                match = re.search(
                    r"cidade=(?P<city>.+?)\s*\|\s*ganho=(?P<points>\d+)\s*RP\s*\|\s*cristal_antes=(?P<before>\d+)\s*\|\s*cristal_depois=(?P<after>\d+)",
                    message,
                )
                if match:
                    city_name = match.group("city").strip()
                    points_gain = _to_int(match.group("points"), 0)
                    crystal = _to_int(match.group("before"), 0)
                    crystal_after = _to_int(match.group("after"), 0)
                    state_label = "Ensaio executado"
            elif "Falha ao conduzir ensaio:" in message:
                state_label = "Erro"
                reason = message.split("Falha ao conduzir ensaio:", 1)[-1].strip()

        return {
            "city_name": city_name,
            "use_athena_scroll": cls._bool_label(inputs.get("use_athena_scroll")),
            "pay_with_ambrosia": cls._bool_label(inputs.get("pay_with_ambrosia")),
            "crystal": crystal,
            "crystal_cost": crystal_cost,
            "crystal_after": crystal_after,
            "points_gain": points_gain,
            "state_label": state_label,
            "reason": reason,
        }

    @classmethod
    def _scientists_display(cls, job, *, child_jobs=None, logs=None):
        inputs = cls._parse_inputs(job)
        if int(job.action_code) != 27:
            return {}

        logs = list(logs or [])
        changed = []
        skipped = []
        failed = []
        for message in logs:
            if message.startswith("Cientistas ajustados: "):
                match = re.search(
                    r"cidade=(?P<city>.+?)\s*\|\s*antes=(?P<before>\d+)\s*\|\s*depois=(?P<after>\d+)",
                    message,
                )
                if match:
                    changed.append({
                        "city_name": match.group("city").strip(),
                        "before": _to_int(match.group("before"), 0),
                        "after": _to_int(match.group("after"), 0),
                    })
            elif message.startswith("Cientistas: "):
                match = re.search(
                    r"cidade=(?P<city>.+?)\s*\|\s*atual=(?P<current>\d+)\s*\|\s*alvo=(?P<target>\d+)\s*\|\s*max=(?P<max>\d+)\s*\|\s*cidadaos=(?P<citizens>\d+)",
                    message,
                )
                if match and not any(item["city_name"] == match.group("city").strip() for item in changed):
                    skipped.append({
                        "city_name": match.group("city").strip(),
                        "current": _to_int(match.group("current"), 0),
                        "target": _to_int(match.group("target"), 0),
                        "max": _to_int(match.group("max"), 0),
                        "citizens": _to_int(match.group("citizens"), 0),
                    })
            elif message.startswith("Falha ao ajustar cientistas em "):
                match = re.search(r"Falha ao ajustar cientistas em (?P<city>.+?): (?P<reason>.+)$", message)
                if match:
                    failed.append({
                        "city_name": match.group("city").strip(),
                        "reason": match.group("reason").strip(),
                    })

        return {
            "target_mode_label": SCIENTIST_TARGET_MODE_META.get(str(inputs.get("target_mode") or "absolute"), "Quantidade fixa"),
            "target_value": _to_int(inputs.get("target_value"), 0),
            "reserve_citizens": _to_int(inputs.get("reserve_citizens"), 0),
            "requested_cities": [str(city).strip() for city in (inputs.get("cities") or []) if str(city).strip()],
            "changed": changed,
            "skipped": skipped,
            "failed": failed,
            "state_label": "Ajustado" if changed and not failed else ("Parcial" if changed and failed else ("Erro" if failed else "Sem mudancas")),
        }

    @classmethod
    def _city_label(cls, job):
        inputs = cls._parse_inputs(job)

        city_name = str(inputs.get("city_name") or "").strip()
        city_id = str(inputs.get("city_id") or "").strip()
        plan = inputs.get("construction_plan_json")
        if isinstance(plan, list) and plan:
            city_names = []
            for step in plan:
                name = str(step.get("city_name") or step.get("city_id") or "").strip()
                if name and name not in city_names:
                    city_names.append(name)
            if len(city_names) == 1:
                return city_names[0]
            if city_names:
                return "Multiplas"
        if city_name:
            return city_name
        if city_id:
            return city_id

        source_name = str(inputs.get("from_city_name") or "").strip()
        target_name = str(inputs.get("to_city_name") or "").strip()
        source_id = str(inputs.get("from_city") or "").strip()
        target_id = str(inputs.get("to_city") or "").strip()
        if source_name or source_id or target_name or target_id:
            origin = source_name or source_id or "?"
            destination = target_name or target_id or "?"
            return f"{origin} -> {destination}"

        cities = inputs.get("cities")
        if isinstance(cities, list) and cities:
            return "Multiplas"
        return ""

    @classmethod
    def _resource_items(cls, job):
        inputs = cls._parse_inputs(job)

        items = []
        summary = inputs.get("construction_summary")
        if isinstance(summary, dict):
            planned_totals = summary.get("totals") or {}
            for key, (label, icon) in RESOURCE_ICON_MAP.items():
                lookup_key = "glas" if key == "crystal" else key
                amount = int(planned_totals.get(lookup_key, 0) or 0)
                if amount > 0:
                    items.append({"key": key, "label": label, "icon": icon, "amount": amount})
            if items:
                return items
        for key, (label, icon) in RESOURCE_ICON_MAP.items():
            amount = int(inputs.get(key, 0) or 0)
            if amount > 0:
                items.append({"key": key, "label": label, "icon": icon, "amount": amount})
        return items

    @staticmethod
    def _resource_total_items(counter):
        items = []
        for key in ["wood", "wine", "marble", "crystal", "sulfur"]:
            amount = int(counter.get(key, 0))
            if amount > 0:
                label, icon = RESOURCE_ICON_MAP[key]
                items.append({"key": key, "label": label, "icon": icon, "amount": amount})
        return items

    @staticmethod
    def _city_summary(city_labels):
        if not city_labels:
            return "-"
        if len(city_labels) == 1:
            return city_labels[0]
        return f"Multiplas ({len(city_labels)})"

    @staticmethod
    def _status_summary(counter):
        parts = []
        for status in ["running", "queued", "scheduled", "finished", "error", "cancelled"]:
            amount = int(counter.get(status, 0))
            if amount:
                parts.append(f"{amount} {status}")
        return " | ".join(parts) if parts else "0"

    def _summary_lines(self, entry):
        if entry["group_type"] == "construction":
            return self._construction_summary_lines(entry)
        if entry["group_type"] == "transport":
            return self._transport_summary_lines(entry)
        return []

    def _construction_summary_lines(self, entry):
        root_job = entry["root_job"]
        inputs = self._parse_inputs(root_job)
        steps = inputs.get("construction_plan_steps") or inputs.get("construction_plan_json") or []
        if not isinstance(steps, list) or not steps:
            return []

        lines = [f"{len(steps)} etapa(s) no plano"]
        progress = _parse_json_object(getattr(root_job, "progress_json", "{}"))
        progress_meta = progress.get("metadata") if isinstance(progress.get("metadata"), dict) else {}
        waiting_items = progress_meta.get("waiting") if isinstance(progress_meta.get("waiting"), list) else []
        started_items = progress_meta.get("started") if isinstance(progress_meta.get("started"), list) else []
        if started_items:
            lines.append(f"{len(started_items)} obra(s) iniciada(s) no ultimo ciclo")
        if waiting_items:
            lines.append(f"{len(waiting_items)} cidade(s) em espera")
        current_message = ""
        blocker_message = ""

        for item in sorted(entry["jobs"], key=lambda row: row["object"].created_at, reverse=True):
            recent_logs = entry.get("recent_log_messages", {}).get(item["object"].pk, [])
            for msg in recent_logs:
                if "Evolucao iniciada:" in msg or "Construcao iniciada:" in msg:
                    current_message = msg.split(":", 1)[-1].strip()
                    break
            if current_message:
                break
            for msg in recent_logs:
                lowered = msg.lower()
                if "aguardando" in lowered or "sem recurso" in lowered or "obra" in lowered:
                    blocker_message = msg
                    break
            if blocker_message:
                break

        if current_message:
            lines.append(f"Atual: {current_message}")
        else:
            first = steps[0]
            lines.append(
                f"Meta: {first.get('building_name') or first.get('building_id')} -> {int(first.get('target_level') or 0)}"
            )
        if blocker_message:
            lines.append(f"Bloqueio: {blocker_message}")
        return lines

    def _transport_summary_lines(self, entry):
        root_job = entry["root_job"]
        inputs = self._parse_inputs(root_job)
        transport = self._transport_display(root_job, child_jobs=[item["object"] for item in entry["jobs"][1:]])
        route = transport.get("route") or self._city_label(root_job)
        lines = []
        if route and route != "-":
            lines.append(route)
        if transport.get("mode_label"):
            lines.append(transport["mode_label"])
        if transport.get("eta_items"):
            total_eta = next((item for item in transport["eta_items"] if item["label"] == "ETA total"), None)
            if total_eta:
                lines.append(f"ETA: {total_eta['human']}")
        if transport.get("next_check_human"):
            lines.append(f"Proximo check: {transport['next_check_human']}")
        if inputs.get("monitor_mode") == "arrival_check":
            lines.append("Monitor de chegada")
        else:
            amount = sum(int(inputs.get(key, 0) or 0) for key in ("wood", "wine", "marble", "crystal", "sulfur"))
            if amount:
                lines.append(f"Remessa raiz: {amount:,}")
        if transport.get("pending_total"):
            lines.append(f"Pendente: {transport['pending_total']:,}")
        if entry["status_counts"].get("scheduled") and not entry["status_counts"].get("running"):
            lines.append("Aguardando chegada ou continuação")
        return lines


    @staticmethod
    def _train_display(job):
        if int(job.action_code) != 1005:
            return {}
        try:
            inputs = json.loads(job.inputs_json or "{}")
        except Exception:
            return {}

        from core.catalogs import TRAINING_UNITS, UNIT_ICON_MAP
        building_type = str(inputs.get("building_type") or "troops")
        mode = str(inputs.get("mode") or "once")
        maintain_scope = str(inputs.get("maintain_scope") or "local")
        city_id = inputs.get("city_id")
        city_ids = inputs.get("city_ids") or []
        city_targets_raw = inputs.get("city_targets") or {}

        catalog = {str(u["id"]): u for bt in TRAINING_UNITS.values() for u in bt}

        def _make_rows(units_dict):
            rows = []
            tots = {"wood": 0, "sulfur": 0, "wine": 0, "crystal": 0, "upkeep": 0}
            for uid_str, qty in units_dict.items():
                qty = int(qty or 0)
                if qty <= 0:
                    continue
                meta = catalog.get(str(uid_str)) or {}
                name = meta.get("name") or f"Unidade {uid_str}"
                icon_path = UNIT_ICON_MAP.get(name) or UNIT_ICON_MAP.get(meta.get("css") or "") or ""
                row = {
                    "name": name, "qty": qty, "icon": icon_path,
                    "wood": (meta.get("wood") or 0) * qty,
                    "sulfur": (meta.get("sulfur") or 0) * qty,
                    "wine": (meta.get("wine") or 0) * qty,
                    "crystal": (meta.get("crystal") or 0) * qty,
                    "upkeep": (meta.get("upkeep") or 0) * qty,
                }
                rows.append(row)
                for res in ("wood", "sulfur", "wine", "crystal", "upkeep"):
                    tots[res] += row[res]
            return rows, tots

        # Per-city breakdown for selected_custom
        city_breakdown = []
        if maintain_scope == "selected_custom" and city_ids:
            uniform_target = inputs.get("target_garrison") or {}
            for cid in city_ids:
                # Use custom target if available, else fall back to uniform
                ct = city_targets_raw.get(str(cid)) or uniform_target
                if ct:
                    rows, tots = _make_rows(ct)
                    label = "custom" if str(cid) in city_targets_raw else "padrao"
                    if rows:
                        city_breakdown.append({"city_id": str(cid), "unit_rows": rows, "totals": tots, "label": label})

        # Fallback: uniform target or once units
        units_raw = inputs.get("units") or inputs.get("target_garrison") or {}
        unit_rows, totals = _make_rows(units_raw)

        return {
            "unit_rows": unit_rows,
            "totals": totals,
            "city_breakdown": city_breakdown,
            "building_type": building_type,
            "mode": mode,
            "maintain_scope": maintain_scope,
            "city_id": city_id,
            "city_count": len(city_ids) if city_ids else (1 if city_id else 0),
        }

    @staticmethod
    def _station_display(job):
        if int(job.action_code) != 1202:
            return {}
        try:
            inputs = json.loads(job.inputs_json or "{}")
        except Exception:
            return {}

        from core.catalogs import TRAINING_UNITS, UNIT_ICON_MAP
        catalog = {str(u["id"]): u for bt in TRAINING_UNITS.values() for u in bt}
        units_raw = inputs.get("units") or {}

        unit_rows = []
        for uid_str, qty in units_raw.items():
            qty = int(qty or 0)
            if qty <= 0:
                continue
            meta = catalog.get(str(uid_str)) or {}
            name = meta.get("name") or f"Unidade {uid_str}"
            uid = int(uid_str)
            unit_type = "fleet" if 200 <= uid < 300 else "troops"
            icon_path = UNIT_ICON_MAP.get(name) or UNIT_ICON_MAP.get(meta.get("css") or "") or ""
            unit_rows.append({"name": name, "qty": qty, "type": unit_type, "icon": icon_path})

        return {
            "unit_rows": unit_rows,
            "from_city_id": inputs.get("from_city_id"),
            "to_city_id": inputs.get("to_city_id"),
        }

    @classmethod
    def _barbarians_display(cls, job):
        inputs = cls._parse_inputs(job)
        if int(job.action_code) != 19:
            return None
        island_ids_raw = str(inputs.get("island_ids") or inputs.get("island_id") or "")
        island_list = [s.strip() for s in island_ids_raw.split(",") if s.strip()]
        return {
            "phase": inputs.get("phase") or "check",
            "island_list": island_list,
            "island_index": _to_int(inputs.get("island_index"), 0),
            "total_attacks": _to_int(inputs.get("total_attacks"), 0),
            "total_loots": _to_int(inputs.get("total_loots"), 0),
            "last_barb_level": inputs.get("last_barb_level"),
            "last_attack_at": inputs.get("last_attack_at"),
            "last_loot_at": inputs.get("last_loot_at"),
            "resources_looted": inputs.get("last_resources_looted"),
        }

    @classmethod
    def _piracy_display(cls, job):
        inputs = cls._parse_inputs(job)
        if int(job.action_code) != 17:
            return {}
        _MISSION_LEVEL_LABELS = {
            1: "Nv 1 — 2min 30s", 3: "Nv 3 — 7min 30s", 5: "Nv 5 — 15min",
            7: "Nv 7 — 30min", 9: "Nv 9 — 1h", 11: "Nv 11 — 2h",
            13: "Nv 13 — 4h", 15: "Nv 15 — 8h", 17: "Nv 17 — 16h",
        }
        _CONVERT_MODE_LABELS = {
            "never": "Nunca converter", "all": "Converter tudo automaticamente",
            "percent": "Converter percentual do ganho", "threshold": "Converter por limite",
        }
        capture_points = inputs.get("last_capture_points")
        crew_points = inputs.get("last_crew_points")
        complete_crew_points = inputs.get("last_complete_crew_points")
        fortress_level = inputs.get("last_fortress_level")
        time_remaining = _to_int(inputs.get("last_time_remaining"), 0)
        schedule_by_time = bool(inputs.get("schedule_by_time"))
        day_level = _to_int(inputs.get("day_mission_level"), 7)
        night_level = _to_int(inputs.get("night_mission_level"), 13)
        day_start = _to_int(inputs.get("day_start_hour"), 8)
        day_end = _to_int(inputs.get("day_end_hour"), 22)
        convert_mode = str(inputs.get("convert_mode") or "never")
        highscore_entries = inputs.get("piracy_highscore_entries") if isinstance(inputs.get("piracy_highscore_entries"), list) else []
        highscore_self = inputs.get("piracy_highscore_self") if isinstance(inputs.get("piracy_highscore_self"), dict) else {}
        return {
            "capture_points": capture_points,
            "crew_points": crew_points,
            "complete_crew_points": complete_crew_points,
            "fortress_level": fortress_level,
            "time_remaining": time_remaining,
            "time_remaining_human": _duration_human(time_remaining) if time_remaining > 0 else "",
            "in_mission": time_remaining > 0,
            "total_missions": _to_int(inputs.get("total_missions_started"), 0),
            "total_crew_converted": _to_int(inputs.get("total_crew_converted"), 0),
            "schedule_by_time": schedule_by_time,
            "day_level_label": _MISSION_LEVEL_LABELS.get(day_level, f"Nv {day_level}"),
            "night_level_label": _MISSION_LEVEL_LABELS.get(night_level, f"Nv {night_level}"),
            "day_hours": f"{day_start}h–{day_end}h",
            "night_hours": f"{day_end}h–{day_start}h",
            "mission_level_label": _MISSION_LEVEL_LABELS.get(
                _to_int(inputs.get("mission_level"), 7), f"Nv {inputs.get('mission_level', 7)}"
            ),
            "convert_mode_label": _CONVERT_MODE_LABELS.get(convert_mode, convert_mode),
            "convert_percent": _to_int(inputs.get("convert_percent"), 100),
            "convert_threshold": _to_int(inputs.get("convert_threshold"), 0),
            "enable_targeted_cycle": bool(inputs.get("enable_targeted_cycle")),
            "targeted_interval_days": _to_int(inputs.get("targeted_interval_days"), 7),
            "targeted_after_hour": _to_int(inputs.get("targeted_after_hour"), 16),
            "target_require_lower_total_score": bool(inputs.get("target_require_lower_total_score")),
            "target_require_lower_general_score": bool(inputs.get("target_require_lower_general_score")),
            "target_max_score_gap": _to_int(inputs.get("target_max_score_gap"), 5),
            "targeted_max_active_foundations": _to_int(inputs.get("targeted_max_active_foundations"), 1),
            "highscore_entries": highscore_entries[:10],
            "highscore_self": highscore_self,
            "highscore_time_left": _to_int(inputs.get("piracy_highscore_time_left"), 0),
            "highscore_updated_at": _to_int(inputs.get("piracy_highscore_updated_at"), 0),
            "has_state": capture_points is not None,
        }

    @classmethod
    def _resolve_city_name(cls, job, city_id) -> str:
        """Lookup city name in the game_account's snapshot."""
        if not city_id or not job.game_account_id:
            return ""
        try:
            from apps.game.models import AccountSnapshot
            snap = AccountSnapshot.objects.filter(game_account_id=job.game_account_id).first()
            if not snap:
                return ""
            for c in (snap.cities or []):
                if str(c.get("id")) == str(city_id):
                    return str(c.get("name") or "")
        except Exception:
            pass
        return ""

    @classmethod
    def _spy_display(cls, job):
        inputs = cls._parse_inputs(job)
        if int(job.action_code) != 15:
            return {}

        _SPY_MISSIONS = {
            1:  "Enviar espião",
            3:  "Nível de pesquisa",
            5:  "Inspecionar armazém",
            6:  "Guarnição militar",
            7:  "Tropas e frotas",
            8:  "Chamar espião",
            10: "Observar comunicação",
            21: "Ver estado",
            23: "Produção militar",
            24: "Cargo na aliança",
            25: "Forma de governo",
            26: "Invenções",
            27: "Colônias",
        }
        _PHASE_LABELS = {
            "accumulating":      ("Acumulando",  "badge-warning"),
            "executing":         ("Executando",  "badge-info"),
            "recalling":         ("Chamando de volta", "badge-secondary"),
            "done":              ("Concluído",   "badge-success"),
            "infiltrating_only": ("Infiltrando", "badge-warning"),
        }

        recovery = inputs.get("__recovery") if isinstance(inputs.get("__recovery"), dict) else {}
        phase        = str(recovery.get("phase") or inputs.get("phase") or "accumulating")
        phase_label, phase_badge = _PHASE_LABELS.get(phase, (phase.capitalize(), "badge-secondary"))

        # Mission list
        raw_mid = inputs.get("mission_id") or "1"
        try:
            if isinstance(raw_mid, list):
                all_missions = [int(x) for x in raw_mid]
            else:
                all_missions = [int(x.strip()) for x in str(raw_mid).split(",") if x.strip()]
        except Exception:
            all_missions = [1]
        intel_missions = [m for m in all_missions if m not in (1, 8)]

        missions_done    = list(recovery.get("missions_done") or [])
        missions_pending = list(recovery.get("missions_pending") or intel_missions)
        mission_risks    = dict(recovery.get("mission_risks") or {})

        # Recovery state
        no_progress     = int(recovery.get("no_progress_cycles") or 0)
        last_stationed  = int(recovery.get("last_stationed") or 0)
        remaining_risk  = float(recovery.get("prev_remaining_risk") or 0)
        sent_total      = int(recovery.get("sent_total") or 0)
        arrival_at      = int(recovery.get("arrival_at") or 0)
        import time as _time
        import datetime as _dt
        from django.utils import timezone as _tz
        _now_ts = int(_time.time())
        arrival_in_s    = max(0, arrival_at - _now_ts) if arrival_at else 0
        recall_after    = bool(inputs.get("recall_after", True))

        # Absolute arrival datetime (local-aware) — used for tooltip / prediction label
        arrival_dt_str = ""
        if arrival_at:
            _arr_dt = _dt.datetime.fromtimestamp(arrival_at, tz=_dt.timezone.utc)
            try:
                _arr_local = _arr_dt.astimezone(_tz.get_current_timezone())
            except Exception:
                _arr_local = _arr_dt
            arrival_dt_str = _arr_local.strftime("%d/%m %H:%M")

        # ── Missão 1: Infiltração (sempre automática) ──────────────────────────
        if phase == "accumulating":
            # Spies still traveling OR being sent
            infil_status = "pending"
            infil_badge  = "badge-warning"
            infil_icon   = "bi-hourglass-split"
            infil_note   = (
                f"{sent_total} espião(ões) enviado(s) — chega em {_duration_human(arrival_in_s)}"
                f" ({arrival_dt_str})" if arrival_in_s > 0 and sent_total > 0
                else (f"{sent_total} espião(ões) enviado(s)" if sent_total > 0 else "aguardando envio")
            )
        else:
            infil_status = "done"
            infil_badge  = "badge-success"
            infil_icon   = "bi-check-circle-fill"
            infil_note   = f"{last_stationed} estacionado(s)" if last_stationed > 0 else ""

        infil_row = {
            "id":               1,
            "name":             "Infiltração",
            "status":           infil_status,
            "badge":            infil_badge,
            "icon":             infil_icon,
            "risk":             None,
            "is_infiltration":  True,
            "detail_note":      infil_note,
            "sent":             sent_total,
            "arrival_in_human": _duration_human(arrival_in_s) if arrival_in_s > 0 else "",
            "arrival_dt_str":   arrival_dt_str,
        }

        # ── Missões de inteligência ────────────────────────────────────────────
        mission_rows = [infil_row]
        for mid in intel_missions:
            if mid in missions_done:
                mstatus = "done"
                mbadge  = "badge-success"
                micon   = "bi-check-circle-fill"
            elif mid in missions_pending:
                mstatus = "pending"
                mbadge  = "badge-secondary"
                micon   = "bi-circle"
            else:
                mstatus = "skipped"
                mbadge  = "badge-danger"
                micon   = "bi-x-circle"
            risk_data = mission_risks.get(str(mid))
            mission_rows.append({
                "id":              mid,
                "name":            _SPY_MISSIONS.get(mid, f"Missão {mid}"),
                "status":          mstatus,
                "badge":           mbadge,
                "icon":            micon,
                "risk":            risk_data,
                "is_infiltration": False,
                "is_recall":       False,
                "detail_note":     "",
            })

        # ── Missão 8: Retirada (se recall_after=True) ─────────────────────────
        if recall_after:
            if phase == "done":
                rec_status = "done"
                rec_badge  = "badge-success"
                rec_icon   = "bi-check-circle-fill"
                rec_note   = "Espiões chamados de volta"
            elif phase == "recalling":
                rec_status = "pending"
                rec_badge  = "badge-warning"
                rec_icon   = "bi-hourglass-split"
                rec_note   = (
                    f"Em retorno — chega em {_duration_human(arrival_in_s)} ({arrival_dt_str})"
                    if arrival_in_s > 0
                    else "Em retorno"
                )
            else:
                rec_status = "pending"
                rec_badge  = "badge-secondary"
                rec_icon   = "bi-circle"
                rec_note   = "Aguardando conclusão das missões de inteligência"
            mission_rows.append({
                "id":              8,
                "name":            "Retirada",
                "status":          rec_status,
                "badge":           rec_badge,
                "icon":            rec_icon,
                "risk":            None,
                "is_infiltration": False,
                "is_recall":       True,
                "detail_note":     rec_note,
            })

        # ── ETA para execução das missões de inteligência ─────────────────────
        # Quando phase=="accumulating", as missões ainda não rodaram — arrival_at é quando vão rodar
        missions_eta_human = ""
        missions_eta_dt    = ""
        if phase == "accumulating" and arrival_in_s > 0:
            missions_eta_human = _duration_human(arrival_in_s)
            missions_eta_dt    = arrival_dt_str
        elif phase in ("executing", "recalling", "done") and job.scheduled_for:
            delta = job.scheduled_for - _tz.now()
            secs  = max(0, int(delta.total_seconds()))
            missions_eta_human = _duration_human(secs) if secs > 0 else ""

        # Next scheduled
        next_run_human  = ""
        if job.scheduled_for:
            delta = job.scheduled_for - _tz.now()
            secs  = max(0, int(delta.total_seconds()))
            if secs < 60:
                next_run_human = f"em {secs}s"
            elif secs < 3600:
                next_run_human = f"em {secs//60}min"
            else:
                next_run_human = f"em {secs//3600}h{(secs%3600)//60}min"

        return {
            "phase":              phase,
            "phase_label":        phase_label,
            "phase_badge":        phase_badge,
            "target_owner":       str(inputs.get("target_owner") or "—"),
            "target_owner_id":    str(inputs.get("target_owner_id") or ""),
            "target_owner_url":   (
                f"/intel/players/detail/?owner_id={inputs.get('target_owner_id')}"
                if inputs.get("target_owner_id") else ""
            ),
            "target_city_name":   str(inputs.get("target_city_name") or "—"),
            "target_city_id":     str(inputs.get("target_city_id") or "—"),
            "target_city_url":    (
                f"/intel/players/city/?city_id={inputs.get('target_city_id')}"
                if inputs.get("target_city_id") else ""
            ),
            "island_id":          str(inputs.get("island_id") or "—"),
            "source_city_id":     str(inputs.get("city_id") or "—"),
            "source_city_name":   cls._resolve_city_name(job, inputs.get("city_id")),
            "max_risk":           _to_int(inputs.get("max_detection_risk"), 35),
            "recall_after":       recall_after,
            "save_reports":       bool(inputs.get("save_reports", True)),
            "all_missions":       intel_missions,
            "mission_rows":       mission_rows,
            "missions_done":      missions_done,
            "missions_pending":   missions_pending,
            "done_count":         len(missions_done),
            "total_count":        len(intel_missions),
            "no_progress":        no_progress,
            "last_stationed":     last_stationed,
            "remaining_risk":     remaining_risk,
            "sent_total":         sent_total,
            "arrival_in_s":       arrival_in_s,
            "arrival_in_human":   _duration_human(arrival_in_s) if arrival_in_s > 0 else "",
            "arrival_dt_str":     arrival_dt_str,
            "missions_eta_human": missions_eta_human,
            "missions_eta_dt":    missions_eta_dt,
            "next_run_human":     next_run_human,
        }

    @classmethod
    def _abandon_colony_display(cls, job):
        inputs = cls._parse_inputs(job)
        if int(job.action_code) != 821:
            return {}
        return {
            "city_id": str(inputs.get("city_id") or "").strip(),
            "city_name": str(inputs.get("city_name") or "").strip(),
            "phase": str(inputs.get("_phase") or "start").strip() or "start",
            "captcha_timeout_sec": _to_int(inputs.get("captcha_timeout_sec"), 120),
        }

    @classmethod
    def _construction_display(cls, job, *, logs=None):
        inputs = cls._parse_inputs(job)
        if int(job.action_code) != 1002:
            return {}

        raw_steps = inputs.get("construction_plan_steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raw_steps = inputs.get("construction_plan_json")
        if not isinstance(raw_steps, list):
            raw_steps = []

        summary = inputs.get("construction_summary") if isinstance(inputs.get("construction_summary"), dict) else {}
        logs = list(logs or [])

        # Step status from inputs
        skipped_indices = set(int(i) for i in (inputs.get("skipped_step_indices") or []) if str(i).isdigit())
        blocked_indices = set(int(i) for i in (inputs.get("blocked_step_indices") or []) if str(i).isdigit())

        # Parse ALL recent "Evolução:"/"Construção:" logs → {step_idx: (from_level, to_level)}
        # Use reversed so first match per idx = most recent for that step
        active_step_data: dict[int, dict] = {}
        for message in reversed(logs):
            if ("Evolução:" in message or "Construção:" in message
                    or "Evolucao:" in message or "Construcao:" in message):
                idx_m = re.search(r'#(\d+)\s+de\s+\d+', message)
                if idx_m:
                    idx = int(idx_m.group(1))
                    if idx not in active_step_data:
                        lv_m = re.search(r'Lv\s+(\d+)\s*[→>]\s*(\d+)', message)
                        active_step_data[idx] = {
                            "from_level": int(lv_m.group(1)) if lv_m else None,
                            "to_level": int(lv_m.group(2)) if lv_m else None,
                        }
        # Remove steps already marked as done or blocked
        for idx in skipped_indices | blocked_indices:
            active_step_data.pop(idx, None)

        city_groups = {}
        totals_by_resource = []
        reserved_by_resource = []
        missing_by_resource = []
        all_steps_flat: list[dict] = []

        for idx, step in enumerate(raw_steps, start=1):
            building_id = str(step.get("building_id") or step.get("building_type") or "").strip()
            building_info = get_building_info(building_id) if building_id else {}
            building_name = str(step.get("building_name") or building_info.get("name") or building_id or "?")
            building_icon = building_info.get("icon") or step.get("building_icon") or ""
            city_name = str(step.get("city_name") or step.get("city_id") or "?").strip()
            from_level = _to_int(step.get("from_level"), 0)
            target_level = _to_int(step.get("target_level"), 0)
            adjusted_seconds = _to_int(step.get("adjusted_seconds"), 0)
            base_seconds = _to_int(step.get("base_seconds"), 0)
            mode = str(step.get("mode") or "upgrade").strip().lower()
            preferred_position = str(step.get("preferred_position") or "").strip()

            resource_costs = []
            resource_reserved = []
            resource_missing = []
            totals_map = step.get("totals") or {}
            reserved_map = step.get("reserved_local") or {}
            missing_map = step.get("missing") or {}
            for key, (label, icon) in RESOURCE_ICON_MAP.items():
                lookup_key = "glas" if key == "crystal" else key
                total_amount = _to_int(totals_map.get(lookup_key), 0)
                reserved_amount = _to_int(reserved_map.get(lookup_key), 0)
                missing_amount = _to_int(missing_map.get(lookup_key), 0)
                if total_amount > 0:
                    resource_costs.append({"key": key, "label": label, "icon": icon, "amount": total_amount})
                if reserved_amount > 0:
                    resource_reserved.append({"key": key, "label": label, "icon": icon, "amount": reserved_amount})
                if missing_amount > 0:
                    resource_missing.append({"key": key, "label": label, "icon": icon, "amount": missing_amount})

            # Build per-level queue from level_rows (each row = one level transition)
            raw_level_rows = step.get("level_rows") or []
            level_queue = []
            for lr in raw_level_rows:
                lvl = _to_int(lr.get("level"), 0)
                if lvl <= 0:
                    continue
                lr_costs_raw = lr.get("costs") or {}
                lr_cost_items = []
                for key, (label, icon) in RESOURCE_ICON_MAP.items():
                    lk = "glas" if key == "crystal" else key
                    amt = _to_int(lr_costs_raw.get(lk), 0)
                    if amt > 0:
                        lr_cost_items.append({"key": key, "label": label, "icon": icon, "amount": amt})
                level_queue.append({
                    "from_level": lvl - 1,
                    "to_level": lvl,
                    "adjusted_duration": str(lr.get("adjusted_duration") or ""),
                    "adjusted_seconds": _to_int(lr.get("adjusted_seconds"), 0),
                    "is_current": False,
                    "costs": lr_cost_items,
                })
            has_level_queue = len(level_queue) > 1
            last_to_level = level_queue[-1]["to_level"] if level_queue else target_level

            # Determine step status
            if idx in skipped_indices:
                step_status = "done"
            elif idx in blocked_indices:
                step_status = "blocked"
            elif idx in active_step_data:
                step_status = "active"
            else:
                step_status = "waiting"

            # Duration for active transition (specific level row), not total step
            active_trans = active_step_data.get(idx)
            active_duration_human = ""
            active_resources: list[dict] = []
            if active_trans and active_trans.get("to_level"):
                to_lv = active_trans["to_level"]
                for lrow in level_queue:
                    if lrow["to_level"] == to_lv:
                        active_duration_human = str(lrow.get("adjusted_duration") or "")
                        break
                for lr in raw_level_rows:
                    if _to_int(lr.get("level"), 0) == to_lv:
                        lr_costs = lr.get("costs") or {}
                        for key, (label, icon) in RESOURCE_ICON_MAP.items():
                            lk = "glas" if key == "crystal" else key
                            amt = _to_int(lr_costs.get(lk), 0)
                            if amt > 0:
                                active_resources.append({"key": key, "label": label, "icon": icon, "amount": amt})
                        break

            # Next transition = first level row (what will be built next for waiting steps)
            next_transition: dict | None = None
            next_duration_human = ""
            next_resources: list[dict] = []
            if level_queue:
                first_row = level_queue[0]
                next_transition = {"from_level": first_row["from_level"], "to_level": first_row["to_level"]}
                next_duration_human = str(first_row.get("adjusted_duration") or "")
            if raw_level_rows:
                first_lr_costs = (raw_level_rows[0].get("costs") or {})
                for key, (label, icon) in RESOURCE_ICON_MAP.items():
                    lk = "glas" if key == "crystal" else key
                    amt = _to_int(first_lr_costs.get(lk), 0)
                    if amt > 0:
                        next_resources.append({"key": key, "label": label, "icon": icon, "amount": amt})

            step_display = {
                "index": idx,
                "city_name": city_name,
                "building_name": building_name,
                "building_icon": building_icon,
                "building_id": building_id,
                "mode_label": "Construir novo" if mode == "new" else "Evoluir",
                "level_label": f"Lv {from_level} → {target_level}" if from_level > 0 else f"Novo → Lv {target_level}",
                "level_range_label": f"Lv {from_level} → {last_to_level} ({len(level_queue)} níveis)" if has_level_queue else "",
                "slot_label": f"Slot {preferred_position}" if preferred_position else "",
                "adjusted_human": _duration_human(adjusted_seconds),
                "active_duration_human": active_duration_human,
                "base_human": _duration_human(base_seconds),
                "resource_costs": resource_costs,
                "resource_reserved": resource_reserved,
                "resource_missing": resource_missing,
                "has_shortfall": any(item["amount"] > 0 for item in resource_missing),
                "level_queue": level_queue,
                "has_level_queue": has_level_queue,
                "status": step_status,
                "active_transition": active_trans,
                "active_resources": active_resources,
                "active_duration_human": active_duration_human,
                "next_transition": next_transition,
                "next_duration_human": next_duration_human,
                "next_resources": next_resources,
                "next_level_label": (
                    f"Lv {next_transition['from_level']} → {next_transition['to_level']}"
                    if next_transition else level_label
                ),
                "target_level": target_level,
            }
            city_groups.setdefault(city_name, []).append(step_display)
            all_steps_flat.append(step_display)

        # Also detect active from "X em obra: Building Lv Y→Z" logs (cities already building)
        # These don't have #N index so match by city_name + building_name
        em_obra_re = re.compile(
            r'^(.+?)\s+em\s+obra:\s+(.+?)\s+Lv\s+(\d+)\s*[→>]\s*(\d+)',
            re.IGNORECASE,
        )
        # Build lookup: (city_name_lower, building_name_lower) → step
        step_lookup: dict[tuple[str, str], dict] = {}
        for s in all_steps_flat:
            key = (s["city_name"].strip().lower(), s["building_name"].strip().lower())
            if key not in step_lookup:
                step_lookup[key] = s
        for message in reversed(logs):
            m_eo = em_obra_re.match(message)
            if not m_eo:
                continue
            city_log = m_eo.group(1).strip().lower()
            bld_log = m_eo.group(2).strip().lower()
            from_lv = int(m_eo.group(3))
            to_lv = int(m_eo.group(4))
            # Try exact match first, then partial
            matched = step_lookup.get((city_log, bld_log))
            if matched is None:
                for (c, b), s in step_lookup.items():
                    if c == city_log and (bld_log in b or b in bld_log):
                        matched = s
                        break
            if matched and matched["index"] not in active_step_data and matched["status"] == "waiting":
                active_step_data[matched["index"]] = {"from_level": from_lv, "to_level": to_lv}
                matched["status"] = "active"
                matched["active_transition"] = active_step_data[matched["index"]]
                for lrow in matched.get("level_queue") or []:
                    if lrow["to_level"] == to_lv:
                        matched["active_duration_human"] = str(lrow.get("adjusted_duration") or "")
                        matched["active_resources"] = lrow.get("costs") or []
                        break

        # Compute totals: plan total and remaining (non-done steps only)
        plan_totals: dict[str, int] = {}
        remaining_totals: dict[str, int] = {}
        for i, step in enumerate(raw_steps, start=1):
            step_totals = step.get("totals") or {}
            for key in RESOURCE_ICON_MAP:
                lookup_key = "glas" if key == "crystal" else key
                amt = _to_int(step_totals.get(lookup_key), 0)
                plan_totals[key] = plan_totals.get(key, 0) + amt
                if i not in skipped_indices:
                    remaining_totals[key] = remaining_totals.get(key, 0) + amt

        # Compute spent from completed level_rows per step
        # done steps: all rows spent; active steps: rows with to_level < current active to_level
        spent_level_totals: dict[str, int] = {}
        for s in all_steps_flat:
            lq = s.get("level_queue") or []
            if s["status"] == "done":
                for lrow in lq:
                    for item in (lrow.get("costs") or []):
                        spent_level_totals[item["key"]] = spent_level_totals.get(item["key"], 0) + item["amount"]
            elif s["status"] == "active":
                at = s.get("active_transition") or {}
                curr_to = at.get("to_level")
                if curr_to:
                    for lrow in lq:
                        if lrow["to_level"] < curr_to:
                            for item in (lrow.get("costs") or []):
                                spent_level_totals[item["key"]] = spent_level_totals.get(item["key"], 0) + item["amount"]

        spent_by_resource = []
        for key, (label, icon) in RESOURCE_ICON_MAP.items():
            lookup_key = "glas" if key == "crystal" else key
            total_amount = plan_totals.get(key, 0)
            remaining = remaining_totals.get(key, 0)
            spent_amount = spent_level_totals.get(key, 0)
            reserved_amount = _to_int((summary.get("reserved_local") or {}).get(lookup_key), 0)
            missing_amount = _to_int((summary.get("missing") or {}).get(lookup_key), 0)
            if total_amount > 0:
                totals_by_resource.append({"key": key, "label": label, "icon": icon, "amount": total_amount})
            if reserved_amount > 0:
                reserved_by_resource.append({"key": key, "label": label, "icon": icon, "amount": reserved_amount})
            if missing_amount > 0:
                missing_by_resource.append({"key": key, "label": label, "icon": icon, "amount": missing_amount})
            if total_amount > 0:
                spent_by_resource.append({"key": key, "label": label, "icon": icon, "amount": spent_amount, "remaining": remaining})

        current_message = ""
        blocker_message = ""
        for message in reversed(logs):
            if not current_message and (
                "Evolução:" in message or "Construção:" in message
                or "Evolucao:" in message or "Construcao:" in message
                or "Evolucao iniciada:" in message or "Construcao iniciada:" in message
            ):
                current_message = message
            lowered = message.lower()
            if not blocker_message and (
                "falta" in lowered
                or "aguardando recursos" in lowered
                or "sem recurso" in lowered
                or "nao foi possivel" in lowered
                or "obra em andamento" in lowered
                or "bloqueada" in lowered
            ):
                blocker_message = message
            if current_message and blocker_message:
                break

        city_cards = []
        for city_name_key, city_steps in city_groups.items():
            city_active = next((s for s in city_steps if s["status"] == "active"), None)
            # next = first waiting step after the active one (or any waiting if no active)
            active_idx = city_active["index"] if city_active else -1
            city_next = next((s for s in city_steps if s["status"] == "waiting" and s["index"] > active_idx), None)
            if city_next is None:
                city_next = next((s for s in city_steps if s["status"] == "waiting"), None)
            city_cards.append({
                "city_name": city_name_key,
                "step_count": len(city_steps),
                "done_count": sum(1 for s in city_steps if s["status"] == "done"),
                "shortfall_count": sum(1 for s in city_steps if s["has_shortfall"] and s["status"] not in ("done",)),
                "active_step": city_active,
                "next_step": city_next,
                "open_by_default": bool(city_active),
                "steps": city_steps,
            })
        city_cards.sort(key=lambda item: item["city_name"].lower())

        active_steps = [s for s in all_steps_flat if s["status"] == "active"]
        # next_steps: one per city — first waiting step (after active if any)
        next_steps = [c["next_step"] for c in city_cards if c["next_step"]]
        done_count = sum(1 for s in all_steps_flat if s["status"] == "done")

        return {
            "step_count": len(all_steps_flat),
            "done_count": done_count,
            "city_count": len(city_cards),
            "queue_strategy_label": CONSTRUCTION_QUEUE_STRATEGY_META.get(str(inputs.get("queue_strategy") or "eta_first"), "Ordem do plano"),
            "auto_transport": cls._bool_label(inputs.get("auto_transport", True)),
            "city_cards": city_cards,
            "all_steps": all_steps_flat,
            "active_steps": active_steps,
            "next_steps": next_steps,
            "totals_by_resource": totals_by_resource,
            "reserved_by_resource": reserved_by_resource,
            "missing_by_resource": missing_by_resource,
            "spent_by_resource": spent_by_resource,
            "has_progress": bool(totals_by_resource),
            "base_human": _duration_human(summary.get("base_seconds")),
            "adjusted_human": _duration_human(summary.get("adjusted_seconds")),
            "current_message": current_message,
            "blocker_message": blocker_message,
        }


class JobDetailView(LoginRequiredMixin, DetailView):
    model = Job
    template_name = "jobs/job_detail.html"
    context_object_name = "object"

    def get_queryset(self):
        return super().get_queryset().select_related("account", "node", "profile", "workflow", "workflow_run")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        logs_qs = self.object.logs.all()
        child_jobs = list(Job.objects.filter(source_job_id=self.object.pk).order_by("-created_at"))
        context["logs"] = logs_qs
        context["log_rows"] = _build_log_rows(self.object, list(logs_qs))
        action_info = ACTION_CATALOG.get(self.object.action_code)
        context["action_name"] = action_info["name"] if action_info else None

        # Workflow context for breadcrumb and sidebar
        job_workflow = self.object.workflow
        if job_workflow is None and self.object.root_job_id:
            job_workflow = Workflow.objects.filter(root_job_id=self.object.root_job_id).first()
        if job_workflow is None and self.object.root_job_id is None:
            job_workflow = Workflow.objects.filter(root_job_id=self.object.pk).first()
        context["job_workflow"] = job_workflow
        if job_workflow:
            inputs_wf = WorkflowListView._parse_workflow_json(job_workflow.config_json)
            context["job_workflow_title"] = WorkflowListView._workflow_title(job_workflow, inputs_wf)
            context["job_workflow_status_label"] = dict(Workflow.STATUS_CHOICES).get(job_workflow.status, job_workflow.status)
        context["job_workflow_run"] = self.object.workflow_run

        context["source_job"] = (
            Job.objects.filter(pk=self.object.source_job_id).first()
            if self.object.source_job_id
            else None
        )
        context["child_jobs"] = child_jobs
        context["resource_items"] = JobListView._resource_items(self.object)
        context["transport_route"] = JobListView._city_label(self.object)
        try:
            inputs = json.loads(self.object.inputs_json or "{}")
        except Exception:
            inputs = {}
        context["inputs"] = inputs
        context["inputs_json_pretty"] = json.dumps(inputs, ensure_ascii=False, indent=2, sort_keys=True)
        context["is_transport_job"] = int(self.object.action_code) == 2
        context["transport_display"] = JobListView._transport_display(
            self.object,
            child_jobs=child_jobs,
            logs=list(logs_qs.values_list("message", flat=True)),
        )
        context["donation_display"] = JobListView._donation_display(
            self.object,
            child_jobs=child_jobs,
            logs=list(logs_qs.values_list("message", flat=True)),
        )
        context["shrine_display"] = JobListView._shrine_display(
            self.object,
            child_jobs=child_jobs,
            logs=list(logs_qs.values_list("message", flat=True)),
        )
        context["daily_login_display"] = JobListView._daily_login_display(
            self.object,
            child_jobs=child_jobs,
            logs=list(logs_qs.values_list("message", flat=True)),
        )
        context["attack_alert_display"] = JobListView._attack_alert_display(
            self.object,
            child_jobs=child_jobs,
            logs=list(logs_qs.values_list("message", flat=True)),
        )
        context["research_display"] = JobListView._research_display(
            self.object,
            child_jobs=child_jobs,
            logs=list(logs_qs.values_list("message", flat=True)),
        )
        context["experiment_display"] = JobListView._experiment_display(
            self.object,
            child_jobs=child_jobs,
            logs=list(logs_qs.values_list("message", flat=True)),
        )
        context["scientists_display"] = JobListView._scientists_display(
            self.object,
            child_jobs=child_jobs,
            logs=list(logs_qs.values_list("message", flat=True)),
        )
        context["miracle_display"] = JobListView._miracle_display(
            self.object,
            child_jobs=child_jobs,
            logs=list(logs_qs.values_list("message", flat=True)),
        )
        context["construction_display"] = JobListView._construction_display(
            self.object,
            logs=list(logs_qs.values_list("message", flat=True)),
        )
        context["piracy_display"] = JobListView._piracy_display(self.object)
        context["abandon_colony_display"] = JobListView._abandon_colony_display(self.object)
        context["barbarians_display"] = JobListView._barbarians_display(self.object)
        context["spy_display"] = JobListView._spy_display(self.object)
        context["train_display"] = JobListView._train_display(self.object)
        context["station_display"] = JobListView._station_display(self.object)
        context["construction_plan"] = inputs.get("construction_plan_json") if isinstance(inputs.get("construction_plan_json"), list) else []
        context["construction_summary"] = inputs.get("construction_summary") if isinstance(inputs.get("construction_summary"), dict) else {}
        context["construction_reservations"] = self.object.construction_reservations.filter(status="active")
        context["progress"] = _parse_json_object(self.object.progress_json or "{}")
        context["can_execute_now"] = _can_execute_now(self.object)
        context["can_retry"] = _can_retry(self.object)
        return context


class JobLogsPartialView(LoginRequiredMixin, View):
    """HTMX partial: returns only the logs div for polling."""

    def get(self, request, pk):
        from django.template.loader import render_to_string

        job = get_object_or_404(Job, pk=pk)
        logs = job.logs.all()
        html = render_to_string(
            "jobs/partials/job_logs.html",
            {"object": job, "logs": logs, "log_rows": _build_log_rows(job, list(logs))},
            request=request,
        )
        return HttpResponse(html)


class JobChainHistoryPartialView(LoginRequiredMixin, View):
    """HTMX partial: returns chain history rows only when expanded."""

    def get(self, request, pk):
        from django.template.loader import render_to_string

        root_job = get_object_or_404(
            Job.objects.select_related("account", "game_account", "node"),
            pk=pk,
        )
        child_jobs = list(
            Job.objects.filter(root_job_id=root_job.pk)
            .select_related("account", "game_account", "node")
            .order_by("-created_at")
        )
        html = render_to_string(
            "jobs/partials/job_chain_history.html",
            {
                "root_job": root_job,
                "child_jobs": child_jobs,
                "child_count": len(child_jobs),
            },
            request=request,
        )
        return HttpResponse(html)


class JobCancelView(LoginRequiredMixin, View):
    def post(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        now = timezone.now()
        chain_qs = _chain_jobs_qs(job)
        chain_qs.filter(status__in=("queued", "running", "scheduled")).update(
            status="cancelled",
            finished_at=now,
            updated_at=now,
            lease_expires_at=None,
        )
        _cancel_active_construction_reservations_for_jobs(chain_qs)
        return redirect("jobs:job-detail", pk=job.pk)


class JobRunNowView(LoginRequiredMixin, View):
    def post(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        if not _can_execute_now(job):
            return redirect("jobs:job-detail", pk=job.pk)

        with transaction.atomic():
            locked = Job.objects.select_for_update().get(pk=job.pk)
            if not _can_execute_now(locked):
                return redirect("jobs:job-detail", pk=locked.pk)

            locked.status = "cancelled"
            locked.finished_at = timezone.now()
            locked.exit_code = 97
            locked.save(update_fields=["status", "finished_at", "exit_code", "updated_at"])

            immediate_job = create_job_with_workflow(
                account=locked.account,
                game_account=locked.game_account,
                node=locked.node,
                profile=locked.profile,
                action_code=locked.action_code,
                inputs=locked.inputs_json,
                timeout_sec=locked.timeout_sec,
                source_job=locked,
                status="queued",
                start_new_run=True,
                trigger_type="manual_run_now",
            )

            JobLog.objects.create(
                job=locked,
                level="info",
                message=f"Agendamento ignorado manualmente; novo job imediato criado: {immediate_job.pk}",
            )

            transaction.on_commit(lambda: dispatch_job(immediate_job, eta=None))

        next_url = request.POST.get("next", "").strip()
        if next_url and next_url.startswith("/"):
            return redirect(next_url)
        return redirect("jobs:job-detail", pk=immediate_job.pk)


class JobRetryView(LoginRequiredMixin, View):
    def post(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        if not _can_retry(job):
            return redirect("jobs:job-detail", pk=job.pk)

        with transaction.atomic():
            locked = Job.objects.select_for_update().get(pk=job.pk)
            if not _can_retry(locked):
                return redirect("jobs:job-detail", pk=locked.pk)

            immediate_job = create_job_with_workflow(
                account=locked.account,
                game_account=locked.game_account,
                node=locked.node,
                profile=locked.profile,
                action_code=locked.action_code,
                inputs=locked.inputs_json,
                timeout_sec=locked.timeout_sec,
                source_job=locked,
                status="queued",
                start_new_run=True,
                trigger_type="manual_retry",
            )

            JobLog.objects.create(
                job=locked,
                level="info",
                message=f"Retry manual solicitado; novo job imediato criado: {immediate_job.pk}",
            )

            transaction.on_commit(lambda: dispatch_job(immediate_job, eta=None))

        next_url = request.POST.get("next", "").strip()
        if next_url and next_url.startswith("/"):
            return redirect(next_url)
        return redirect("jobs:job-detail", pk=immediate_job.pk)


class JobBulkDeleteView(LoginRequiredMixin, View):
    """Delete selected jobs or all jobs via HTMX."""

    def post(self, request):
        delete_all = request.POST.get("delete_all") == "true"
        delete_filtered = request.POST.get("delete_filtered") == "true"
        job_ids = request.POST.getlist("job_ids")

        if delete_all:
            queryset = Job.objects.all()
        elif delete_filtered:
            filterset = JobFilter(request.POST, queryset=Job.objects.all())
            queryset = filterset.qs if filterset.is_valid() else Job.objects.none()
        elif job_ids:
            queryset = Job.objects.filter(pk__in=job_ids)
        else:
            return self._toast("Nenhum job selecionado.", "error")

        count = queryset.count()
        queryset.delete()

        return self._toast(f"{count} job(s) excluido(s).", "success")

    @staticmethod
    def _toast(message: str, toast_type: str = "info") -> HttpResponse:
        trigger = json.dumps({
            "toast": {"type": toast_type, "message": message},
            "jobsDeleted": True,
        })
        resp = HttpResponse(status=204)
        resp["HX-Trigger"] = trigger
        return resp


class WorkflowListView(FilterSortListView):
    model = Workflow
    filterset_class = WorkflowFilter
    template_name = "jobs/workflow_list.html"
    partial_template_name = "jobs/partials/workflow_table.html"
    paginate_by = 50
    ordering_fields = ["status", "updated_at", "last_event_at", "next_scheduled_for", "created_at"]
    default_ordering = ["-updated_at"]

    def get_paginate_by(self, queryset):
        """Allow user to control page size via ?per_page= query param."""
        try:
            per_page = int(self.request.GET.get("per_page") or self.paginate_by)
            return max(5, min(200, per_page))
        except (ValueError, TypeError):
            return self.paginate_by
    queryset = Workflow.objects.select_related("account", "game_account", "node", "active_run")

    def get_queryset(self):
        self._backfill_recent_workflows()
        qs = super().get_queryset()
        # Show archived only when explicitly requested
        if self.request.GET.get("archived") == "1":
            return qs.filter(archived_at__isnull=False)
        return qs.filter(archived_at__isnull=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        workflows = list(context.get("page_obj").object_list if context.get("page_obj") else context.get("object_list", []))
        workflow_ids = [workflow.pk for workflow in workflows]

        # Auto-prune old runs for workflows on this page before building the display maps.
        # This ensures the limit is enforced without requiring the user to open each workflow.
        if workflow_ids:
            self._prune_runs_for_workflows(workflow_ids)

        run_map = {}
        if workflow_ids:
            for run in (
                WorkflowRun.objects.filter(
                    workflow_id__in=workflow_ids,
                    archived_at__isnull=True,
                )
                .annotate(
                    row_number=Window(
                        expression=RowNumber(),
                        partition_by=[F("workflow_id")],
                        order_by=[F("sequence").desc(), F("created_at").desc()],
                    )
                )
                .filter(row_number__lte=3)
                .order_by("workflow_id", "-sequence", "-created_at")
            ):
                run_map.setdefault(run.workflow_id, []).append(run)

        # Load recent jobs per workflow (for preview display).
        # One small query per workflow with LIMIT 5 is much faster than a single
        # unbounded query across all workflows — avoids scanning thousands of rows
        # for long-running workflows that accumulate many history entries.
        recent_job_map: dict = {}

        status_times = {}
        if workflow_ids:
            for row in (
                Job.objects.filter(
                    workflow_id__in=workflow_ids,
                    archived_at__isnull=True,
                )
                .values("workflow_id", "status")
                .annotate(max_t=Max("created_at"))
            ):
                status_times.setdefault(row["workflow_id"], {})[row["status"]] = row["max_t"]

        running_workflow_ids = {
            wf_id for wf_id, statuses in status_times.items()
            if statuses.get("running")
        }
        active_workflow_ids = {
            wf_id for wf_id, statuses in status_times.items()
            if statuses.get("running") or statuses.get("queued") or statuses.get("scheduled")
        }
        _error_times = {
            wf_id: statuses["error"]
            for wf_id, statuses in status_times.items()
            if statuses.get("error")
        }
        error_workflow_ids = set(_error_times.keys())
        # Errors superseded by any newer job (not just active) — compare vs most recent job overall
        _latest_times = {
            wf_id: max(t for t in statuses.values() if t)
            for wf_id, statuses in status_times.items()
            if statuses
        }
        superseded_error_ids = {
            wf_id for wf_id, err_t in _error_times.items()
            if _latest_times.get(wf_id) and _latest_times[wf_id] > err_t
        }

        workflow_rows = [
            self._build_workflow_row(
                workflow,
                run_map.get(workflow.pk, []),
                {},
                recent_job_map.get(workflow.pk, []),
                recent_statuses=set(status_times.get(workflow.pk, {})),
                has_active_job=workflow.pk in active_workflow_ids,
                has_running_job=workflow.pk in running_workflow_ids,
                has_error_job=workflow.pk in error_workflow_ids and workflow.pk not in superseded_error_ids,
            )
            for workflow in workflows
        ]
        # Global queryset = all non-archived workflows, no status filter applied
        # Stats bar always shows full picture regardless of active list filter
        archived_view = self.request.GET.get("archived") == "1"
        context["_global_qs"] = Workflow.objects.filter(
            archived_at__isnull=not archived_view
        )
        context["workflow_rows"] = workflow_rows
        context["workflow_summary"] = self._global_workflow_summary(context)  # also syncs all stale statuses
        context["workflow_groups"] = self._group_by_category(workflow_rows)
        context["job_view_mode"] = "ops"
        return context

    @staticmethod
    def _group_by_category(workflow_rows: list) -> list:
        from collections import defaultdict
        from core.actions.constants import CATEGORY_META
        _ORDER = ["construction", "economy", "automation", "military", "market", "monitoring", "admin"]
        buckets: dict = defaultdict(list)
        for row in workflow_rows:
            cat = row["workflow"].category or "other"
            buckets[cat].append(row)
        groups = []
        for cat in _ORDER:
            if cat in buckets:
                meta = CATEGORY_META.get(cat, {})
                groups.append({
                    "category": cat,
                    "label": meta.get("label", cat.title()),
                    "icon": meta.get("icon", "bi-grid"),
                    "color": meta.get("color", "var(--ik-muted)"),
                    "rows": buckets[cat],
                })
        for cat, rows in buckets.items():
            if cat not in _ORDER:
                groups.append({"category": cat, "label": cat.title(), "icon": "bi-grid", "color": "var(--ik-muted)", "rows": rows})
        return groups

    @staticmethod
    def _prune_runs_for_workflows(workflow_ids: list) -> None:
        """Archive excess WorkflowRuns for each workflow on the current page.

        Reads the ``workflow_max_runs_per_workflow`` setting and, for each
        workflow that exceeds the limit, archives the oldest runs in bulk.
        This keeps the active run count bounded without requiring the user
        to open each workflow individually.
        """
        from apps.settings_app.utils import get_int_setting as _get_int
        max_runs = _get_int("workflow_max_runs_per_workflow", 150)

        # Count active runs per workflow in a single query
        from django.db.models import Count as _Count
        counts = {
            row["workflow_id"]: row["cnt"]
            for row in WorkflowRun.objects
            .filter(workflow_id__in=workflow_ids, archived_at__isnull=True)
            .values("workflow_id")
            .annotate(cnt=_Count("id"))
        }

        now = timezone.now()
        for wf_id, total in counts.items():
            if total <= max_runs:
                continue
            excess = total - max_runs
            old_run_ids = list(
                WorkflowRun.objects
                .filter(workflow_id=wf_id, archived_at__isnull=True)
                .order_by("sequence")
                .values_list("pk", flat=True)[:excess]
            )
            if not old_run_ids:
                continue
            # Cancel any still-queued/scheduled jobs in these runs
            Job.objects.filter(
                workflow_run_id__in=old_run_ids,
                status__in=("scheduled", "queued"),
            ).update(status="cancelled", finished_at=now)
            # Archive all jobs in these runs (makes them invisible without deleting history)
            Job.objects.filter(
                workflow_run_id__in=old_run_ids,
                archived_at__isnull=True,
            ).update(archived_at=now)
            WorkflowRun.objects.filter(pk__in=old_run_ids).update(archived_at=now)

    @staticmethod
    def _purge_legacy_jobs() -> int:
        """Delete ALL finished/error/cancelled jobs that have no workflow (legacy, pre-workflow system).

        Runs in batches of 5000 until none remain. Skips jobs still referenced by
        market orders or generals-bank records. JobLog records are deleted explicitly
        before jobs to avoid slow implicit CASCADE.

        Returns total number of deleted jobs.
        """
        from django.db import connection as _conn

        # IDs referenced by business tables — never delete these
        with _conn.cursor() as cur:
            cur.execute("""
                SELECT sell_job_id FROM market_internalmarketorder WHERE sell_job_id IS NOT NULL
                UNION
                SELECT buy_job_id FROM market_internalmarketorder WHERE buy_job_id IS NOT NULL
                UNION
                SELECT redistribution_job_id FROM market_internalmarketorder WHERE redistribution_job_id IS NOT NULL
                UNION
                SELECT manager_job_id FROM generals_bank_generalsbankcycle WHERE manager_job_id IS NOT NULL
                UNION
                SELECT buy_job_id FROM generals_bank_generalsbankcycle WHERE buy_job_id IS NOT NULL
                UNION
                SELECT sell_job_id FROM generals_bank_generalsbankcycletask WHERE sell_job_id IS NOT NULL
                UNION
                SELECT training_job_id FROM generals_bank_generalsbankcycletask WHERE training_job_id IS NOT NULL
                UNION
                SELECT transport_job_id FROM generals_bank_generalsbankcycletask WHERE transport_job_id IS NOT NULL
            """)
            protected_ids = {row[0] for row in cur.fetchall() if row[0]}

        base_qs = Job.objects.filter(
            workflow_id__isnull=True,
            status__in=("finished", "error", "cancelled"),
        )
        if protected_ids:
            base_qs = base_qs.exclude(pk__in=protected_ids)

        from apps.jobs.models import JobLog as _JobLog
        total_deleted = 0
        while True:
            batch_ids = list(base_qs.values_list("pk", flat=True)[:5000])
            if not batch_ids:
                break
            _JobLog.objects.filter(job_id__in=batch_ids).delete()
            Job.objects.filter(pk__in=batch_ids).delete()
            total_deleted += len(batch_ids)

        return total_deleted

    @staticmethod
    def _backfill_recent_workflows(limit=200):
        # Only backfill ACTIVE jobs — finished/cancelled history is not auto-recreated
        legacy_jobs = list(
            Job.objects.filter(
                workflow__isnull=True,
                status__in=("queued", "running", "scheduled"),
            )
            .values_list("pk", "root_job_id")
            .order_by("-created_at")[:limit]
        )
        # Collect unique chain root IDs
        chain_root_ids = {root_id if root_id else pk for pk, root_id in legacy_jobs}
        processed = set()
        for chain_root_id in chain_root_ids:
            if chain_root_id in processed:
                continue
            processed.add(chain_root_id)
            root_job = (
                Job.objects.filter(pk=chain_root_id)
                .select_related("account", "game_account", "node")
                .first()
            )
            if root_job is not None:
                ensure_workflow_for_job(root_job)

    @staticmethod
    def _workflow_summary(workflow_rows):
        summary = {"total": len(workflow_rows), "active": 0, "waiting": 0, "problem": 0, "paused": 0, "finished": 0}
        for row in workflow_rows:
            key = row["status"] if isinstance(row, dict) else row.status
            if key in summary:
                summary[key] += 1
        return summary

    def _global_workflow_summary(self, context) -> dict:
        """Count effective_status across ALL workflows (ignoring status filter) so stats bar is always global."""
        # Use the full queryset without status filter — stats are always total picture
        base_qs = context.get("_global_qs") or Workflow.objects.filter(archived_at__isnull=True)
        summary = {"total": 0, "active": 0, "waiting": 0, "problem": 0, "paused": 0, "finished": 0, "cancelled": 0}
        for row in base_qs.values("status").annotate(total=Count("id")):
            status = row["status"]
            if status in summary:
                summary[status] = int(row["total"])
        summary["total"] = sum(amount for key, amount in summary.items() if key != "total")
        return summary
    @staticmethod
    def _effective_status(workflow, recent_job_statuses: set) -> str:
        """Derive display status from actual job reality, not stale workflow.status."""
        if "running" in recent_job_statuses:
            return "active"
        if recent_job_statuses & {"queued", "scheduled"}:
            return "waiting"
        if "error" in recent_job_statuses:
            return "problem"
        if recent_job_statuses and recent_job_statuses <= {"finished", "cancelled"}:
            return "finished"
        return workflow.status

    @staticmethod
    def _parse_workflow_json(raw):
        try:
            data = json.loads(raw or "{}")
        except Exception:
            data = {}
        return data if isinstance(data, dict) else {}

    @classmethod
    def _workflow_title(cls, workflow, config):
        action_name = str(config.get("action_name") or "").strip()
        if action_name:
            return action_name
        return workflow.workflow_type.replace("_", " ").title()

    @classmethod
    def _build_workflow_row(cls, workflow, runs, job_map, recent_jobs=None, recent_statuses=None, has_active_job=False, has_running_job=False, has_error_job=False):
        scope = cls._parse_workflow_json(workflow.scope_json)
        config = cls._parse_workflow_json(workflow.config_json)
        active_run = workflow.active_run or (runs[0] if runs else None)
        preview_jobs = job_map.get(active_run.pk, []) if active_run else []
        city_label = scope.get("city_name") or (f"{scope.get('city_count')} cidades" if scope.get("city_count") else "")
        scope_parts = [workflow.account.label]
        if workflow.game_account:
            scope_parts.append(workflow.game_account.name or workflow.game_account.server_id)
        if city_label:
            scope_parts.append(str(city_label))

        # has_active_job checks ALL jobs in the chain — prevents false "finished" when
        # a rescheduled job was created before newer finished jobs (e.g. vinho monitor flow).
        if has_active_job:
            # Error jobs flag as problem even when also active
            if has_error_job:
                effective_status = "problem"
            elif has_running_job:
                effective_status = "active"
            else:
                effective_status = "waiting"
        elif has_error_job:
            effective_status = "problem"
        else:
            recent_statuses = set(recent_statuses or {j.status for j in (recent_jobs or [])})
            # has_error_job=False means any error is superseded — don't let stale error status leak
            recent_statuses.discard("error")
            effective_status = cls._effective_status(workflow, recent_statuses)
            # Recurring workflows with no active jobs = loop stopped = problem
            # "paused"/"cancelled" are explicit user actions; "finished" got there via stale sync
            if (effective_status == "finished"
                    and config.get("recurring")
                    and workflow.status not in ("paused", "cancelled")):
                effective_status = "problem"
        status_choices = dict(Workflow.STATUS_CHOICES)
        recent_active_job = next((j for j in (recent_jobs or []) if j.status in ("running", "queued", "scheduled")), None)

        return {
            "workflow": workflow,
            "title": cls._workflow_title(workflow, config),
            "category_label": CATEGORY_META.get(workflow.category, {}).get("label", workflow.category or "Operacional"),
            "category_icon": CATEGORY_META.get(workflow.category, {}).get("icon", "bi-diagram-3"),
            "scope_label": " / ".join(scope_parts),
            "scope": scope,
            "config": config,
            "status": effective_status,
            "status_label": status_choices.get(effective_status, effective_status),
            "status_border_color": _WORKFLOW_BORDER_COLOR.get(effective_status, "var(--ik-line)"),
            "active_run": active_run,
            "recent_runs": runs,
            "preview_jobs": preview_jobs,
            "recent_active_job": recent_active_job,
            "job_count": len(recent_jobs) if recent_jobs is not None else workflow.jobs.count(),
            "run_count": len(runs),
            "can_pause": effective_status in ("active", "waiting"),
            "can_resume": workflow.status == "paused",
            "can_cancel": effective_status in _WORKFLOW_CANCELABLE,
            "can_delete": workflow.status in _WORKFLOW_DELETABLE,
        }


class WorkflowDetailView(LoginRequiredMixin, DetailView):
    model = Workflow
    template_name = "jobs/workflow_detail.html"
    context_object_name = "workflow"

    def get_queryset(self):
        return super().get_queryset().select_related("account", "game_account", "node", "active_run")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        workflow = self.object
        scope = WorkflowListView._parse_workflow_json(workflow.scope_json)
        config = WorkflowListView._parse_workflow_json(workflow.config_json)
        context["scope"] = scope
        context["config"] = config
        context["workflow_title"] = WorkflowListView._workflow_title(workflow, config)
        context["category_label"] = CATEGORY_META.get(workflow.category, {}).get("label", workflow.category or "Operacional")
        context["category_icon"] = CATEGORY_META.get(workflow.category, {}).get("icon", "bi-diagram-3")
        context["status_label"] = dict(Workflow.STATUS_CHOICES).get(workflow.status, workflow.status)
        context["run_count"] = workflow.runs.count()
        context["can_pause"] = workflow.status in ("active", "waiting")
        context["can_resume"] = workflow.status == "paused"
        context["can_cancel"] = workflow.status in _WORKFLOW_CANCELABLE
        context["can_delete"] = workflow.status in _WORKFLOW_DELETABLE
        return context


class WorkflowRunsPartialView(LoginRequiredMixin, View):
    def get(self, request, pk):
        workflow = get_object_or_404(Workflow, pk=pk)

        # Auto-archive old runs if workflow exceeds the configured limit
        from apps.settings_app.utils import get_int_setting as _get_int
        max_runs = _get_int("workflow_max_runs_per_workflow", 150)
        total_runs = WorkflowRun.objects.filter(workflow=workflow, archived_at__isnull=True).count()
        if total_runs > max_runs:
            excess = total_runs - max_runs
            old_run_ids = list(
                WorkflowRun.objects.filter(workflow=workflow, archived_at__isnull=True)
                .order_by("sequence")
                .values_list("pk", flat=True)[:excess]
            )
            # Cancel any active jobs in these runs before archiving — agent must not execute them
            now_ts = timezone.now()
            Job.objects.filter(
                workflow_run_id__in=old_run_ids,
                status__in=("scheduled", "queued"),
            ).update(status="cancelled", finished_at=now_ts)
            # Archive all jobs in these runs (invisible without deleting history)
            Job.objects.filter(
                workflow_run_id__in=old_run_ids,
                archived_at__isnull=True,
            ).update(archived_at=now_ts)
            WorkflowRun.objects.filter(pk__in=old_run_ids).update(archived_at=now_ts)

        page = max(1, int(request.GET.get("page", 1) or 1))
        per_page = 20
        show_archived = request.GET.get("archived") == "1"

        if show_archived:
            # Show jobs that are archived (either via job.archived_at or their run is archived)
            jobs_qs = (
                Job.objects.filter(workflow=workflow)
                .filter(
                    models.Q(archived_at__isnull=False)
                    | models.Q(workflow_run__archived_at__isnull=False)
                )
                .select_related("account", "game_account")
                .order_by("-created_at")
            )
        else:
            # Show only non-archived jobs
            jobs_qs = (
                Job.objects.filter(workflow=workflow, archived_at__isnull=True)
                .filter(
                    models.Q(workflow_run__isnull=True)
                    | models.Q(workflow_run__archived_at__isnull=True)
                )
                .select_related("account", "game_account")
                .order_by("-created_at")
            )
        archived_count = (
            Job.objects.filter(workflow=workflow)
            .filter(
                models.Q(archived_at__isnull=False)
                | models.Q(workflow_run__archived_at__isnull=False)
            )
            .count()
            if not show_archived else 0
        )
        # Cap visible jobs to avoid showing hundreds of historical entries.
        # Archived jobs are accessible via ?archived=1.
        _JOB_DISPLAY_LIMIT = 100
        all_jobs = list(jobs_qs[:_JOB_DISPLAY_LIMIT])
        total = len(all_jobs)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, total_pages)
        raw_jobs = all_jobs[(page - 1) * per_page: page * per_page]
        job_rows = []
        for job in raw_jobs:
            try:
                inputs = json.loads(job.inputs_json or "{}")
            except Exception:
                inputs = {}
            display_name = ACTION_CATALOG.get(job.action_code, {}).get("name", f"Ação {job.action_code}")
            if job.action_code == 2 and inputs.get("monitor_mode") == "arrival_check":
                display_name = "Monitor de Chegada"
            city = inputs.get("city_name") or inputs.get("from_city_name") or inputs.get("to_city_name") or ""
            if job.action_code in {901, 902, 1006}:
                _dtype_map = {"wood": "Madeira", "tradegood": "Bem de Luxo", "wine": "Vinho",
                              "marble": "Mármore", "crystal": "Cristal", "sulfur": "Enxofre"}
                dtype_label = _dtype_map.get(str(inputs.get("donation_type") or ""), "")
                if dtype_label:
                    city = f"{city} — {dtype_label}" if city else dtype_label
            job_rows.append({
                "job": job,
                "display_name": display_name,
                "city": city,
                "can_cancel": job.status in ("queued", "running", "scheduled"),
                "can_run_now": _can_execute_now(job),
                "can_retry": _can_retry(job),
            })
        return render(request, "jobs/partials/workflow_runs.html", {
            "workflow": workflow,
            "job_rows": job_rows,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
            "show_archived": show_archived,
            "archived_count": archived_count,
        })


class WorkflowRunJobsPartialView(LoginRequiredMixin, View):
    """Child jobs of a given job in the workflow chain."""
    def get(self, request, pk, run_pk):
        workflow = get_object_or_404(Workflow, pk=pk)
        parent_job = get_object_or_404(Job, pk=run_pk)
        jobs = list(
            Job.objects.filter(source_job_id=parent_job.pk)
            .select_related("account", "game_account")
            .order_by("-created_at")
        )
        return render(request, "jobs/partials/workflow_run_jobs.html", {
            "workflow": workflow,
            "parent_job": parent_job,
            "jobs": jobs,
        })


class WorkflowLogsPartialView(LoginRequiredMixin, View):
    def get(self, request, pk):
        workflow = get_object_or_404(Workflow, pk=pk)
        page = max(1, int(request.GET.get("page", 1) or 1))
        per_page = 50
        logs_qs = JobLog.objects.filter(job__workflow=workflow).select_related("job").order_by("-created_at")
        total = logs_qs.count()
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, total_pages)
        logs = list(logs_qs[(page - 1) * per_page: page * per_page])
        return render(request, "jobs/partials/workflow_logs.html", {
            "workflow": workflow,
            "logs": logs,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        })


class WorkflowActionView(LoginRequiredMixin, View):
    _TRANSITIONS = {
        "pause": {"from": {"active", "waiting"}, "to": "paused"},
        "resume": {"from": {"paused"}, "to": "active"},
        "cancel": {"from": _WORKFLOW_CANCELABLE, "to": "cancelled"},
    }

    def post(self, request, pk):
        workflow = get_object_or_404(Workflow, pk=pk)
        action = request.POST.get("workflow_action", "").strip()
        now = timezone.now()
        workflow_jobs = Job.objects.filter(workflow=workflow)

        if action == "delete":
            workflow_jobs.filter(
                status__in=("queued", "running", "scheduled"),
            ).update(status="cancelled", finished_at=now, updated_at=now, lease_expires_at=None)
            _cancel_active_construction_reservations_for_jobs(workflow_jobs)
            workflow.delete()
            resp = HttpResponse(status=200)
            resp["HX-Trigger"] = json.dumps({"toast": {"type": "success", "message": "Workflow excluído."}})
            resp["HX-Redirect"] = "/jobs/"
            return resp

        transition = self._TRANSITIONS.get(action)
        if not transition or workflow.status not in transition["from"]:
            resp = HttpResponse(status=400)
            resp["HX-Trigger"] = json.dumps({"toast": {"type": "error", "message": f"Ação '{action}' não permitida no estado atual ({workflow.status})."}})
            return resp

        workflow.status = transition["to"]
        update_fields = ["status", "updated_at"]
        if action == "pause":
            workflow.paused_at = now
            update_fields.append("paused_at")
        workflow.save(update_fields=update_fields)

        if action == "pause":
            # Cancel only scheduled (future) jobs — let running jobs finish naturally
            workflow_jobs.filter(
                status="scheduled",
            ).update(status="cancelled", finished_at=now, updated_at=now, lease_expires_at=None)
        elif action == "cancel":
            # Cancel all active jobs in the chain
            workflow_jobs.filter(
                status__in=("queued", "running", "scheduled"),
            ).update(status="cancelled", finished_at=now, updated_at=now, lease_expires_at=None)
            _cancel_active_construction_reservations_for_jobs(workflow_jobs)

        labels = {"pause": "Workflow pausado.", "resume": "Workflow retomado.", "cancel": "Workflow cancelado."}
        resp = HttpResponse(status=200)
        resp["HX-Trigger"] = json.dumps({"toast": {"type": "success", "message": labels[action]}})
        resp["HX-Refresh"] = "true"
        return resp


class WorkflowArchiveView(LoginRequiredMixin, View):
    """Archive or unarchive a workflow. Archived workflows hidden from main list."""

    def post(self, request, pk):
        workflow = get_object_or_404(Workflow, pk=pk)
        now = timezone.now()
        if workflow.archived_at:
            workflow.archived_at = None
            msg = "Workflow restaurado."
        else:
            workflow.archived_at = now
            msg = "Workflow arquivado."
        workflow.save(update_fields=["archived_at", "updated_at"])
        resp = HttpResponse(status=200)
        resp["HX-Trigger"] = json.dumps({"toast": {"type": "success", "message": msg}})
        resp["HX-Refresh"] = "true"
        return resp


class WorkflowAutoArchiveView(LoginRequiredMixin, View):
    """Auto-archive workflows inactive for more than N days."""

    def post(self, request):
        try:
            days = max(1, int(request.POST.get("days", 7)))
        except (ValueError, TypeError):
            days = 7
        cutoff = timezone.now() - timedelta(days=days)
        qs = Workflow.objects.filter(
            archived_at__isnull=True,
            status__in=("finished", "cancelled"),
        ).filter(
            models.Q(last_event_at__lt=cutoff) | models.Q(last_event_at__isnull=True, updated_at__lt=cutoff)
        )
        count = qs.count()
        qs.update(archived_at=timezone.now())
        resp = HttpResponse(status=200)
        resp["HX-Trigger"] = json.dumps({"toast": {"type": "success", "message": f"{count} workflow(s) arquivado(s)."}})
        resp["HX-Refresh"] = "true"
        return resp


class WorkflowBulkArchiveView(LoginRequiredMixin, View):
    def post(self, request):
        pks = request.POST.getlist("workflow_ids[]")
        delete_all = request.POST.get("delete_all") == "true"
        if not pks and not delete_all:
            resp = HttpResponse(status=400)
            resp["HX-Trigger"] = json.dumps({"toast": {"type": "error", "message": "Nenhum workflow selecionado."}})
            return resp
        now = timezone.now()
        if delete_all:
            qs = Workflow.objects.filter(archived_at__isnull=True)
        else:
            qs = Workflow.objects.filter(pk__in=pks, archived_at__isnull=True)
        count = qs.update(archived_at=now)
        resp = HttpResponse(status=200)
        resp["HX-Trigger"] = json.dumps({"toast": {"type": "success", "message": f"{count} workflow(s) arquivado(s)."}})
        resp["HX-Refresh"] = "true"
        return resp


class WorkflowBulkDeleteView(LoginRequiredMixin, View):
    def post(self, request):
        pks = request.POST.getlist("workflow_ids[]")
        delete_all = request.POST.get("delete_all") == "true"

        if not pks and not delete_all:
            resp = HttpResponse(status=400)
            resp["HX-Trigger"] = json.dumps({"toast": {"type": "error", "message": "Nenhum workflow selecionado."}})
            return resp

        now = timezone.now()

        if delete_all:
            qs = Workflow.objects.all()
        else:
            qs = Workflow.objects.filter(pk__in=pks)

        workflow_jobs = Job.objects.filter(workflow__in=qs)
        workflow_jobs.filter(
            status__in=("queued", "running", "scheduled"),
        ).update(status="cancelled", finished_at=now, updated_at=now, lease_expires_at=None)
        _cancel_active_construction_reservations_for_jobs(workflow_jobs)

        deleted_count, _ = qs.delete()
        msg = f"{deleted_count} workflow(s) excluído(s)."

        resp = HttpResponse(status=200)
        resp["HX-Trigger"] = json.dumps({"toast": {"type": "success", "message": msg}})
        resp["HX-Refresh"] = "true"
        return resp


class WorkflowAwareJobListView(View):
    def get(self, request, *args, **kwargs):
        if request.GET.get("view", "").strip().lower() == "tech":
            return JobListView.as_view()(request, *args, **kwargs)
        return WorkflowListView.as_view()(request, *args, **kwargs)
