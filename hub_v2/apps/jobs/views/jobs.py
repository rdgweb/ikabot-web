"""
Job list, detail, cancel, and bulk-delete views.
"""

import json
import re
from collections import Counter

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.db import transaction
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView

from core.catalogs import get_building_info
from core.contracts import ACTION_CATALOG, RESOURCE_CHOICES
from core.mixins.views import FilterSortListView
from ..filters import JobFilter
from ..models import Job, JobLog
from ..services.dispatch import dispatch_job


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
        context["grouped_rows"] = self._build_grouped_rows(page_jobs, active_descendants)
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

    def _build_grouped_rows(self, jobs, active_descendants=None):
        parent_map = self._load_parent_map(jobs)
        active_descendants = active_descendants or {}
        grouped = {}
        ordered_keys = []

        for job in jobs:
            action_name = self._action_name(job.action_code)
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
                    # Latest active descendant when the root itself is terminal
                    "chain_active_job": active_descendants.get(root_job.pk),
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
            # When root is terminal but chain is still active, reflect the active status
            chain_job = entry["chain_active_job"]
            if chain_job:
                display_counts = Counter({chain_job.status: 1})
            else:
                display_counts = entry["status_counts"]
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
        return "generic"

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
    def _action_name(action_code):
        action_info = ACTION_CATALOG.get(int(action_code))
        return action_info["name"] if action_info else f"Acao #{action_code}"

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
        current_message = ""
        blocker_message = ""

        for item in sorted(entry["jobs"], key=lambda row: row["object"].created_at, reverse=True):
            recent_logs = list(
                JobLog.objects.filter(job=item["object"]).order_by("-created_at").values_list("message", flat=True)[:6]
            )
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

        city_groups = {}
        totals_by_resource = []
        reserved_by_resource = []
        missing_by_resource = []

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

            step_display = {
                "index": idx,
                "city_name": city_name,
                "building_name": building_name,
                "building_icon": building_icon,
                "mode_label": "Construir novo" if mode == "new" else "Evoluir",
                "level_label": f"Lv {from_level} -> {target_level}" if from_level > 0 else f"Novo -> Lv {target_level}",
                "slot_label": f"Slot {preferred_position}" if preferred_position else "",
                "adjusted_human": _duration_human(adjusted_seconds),
                "base_human": _duration_human(base_seconds),
                "resource_costs": resource_costs,
                "resource_reserved": resource_reserved,
                "resource_missing": resource_missing,
                "has_shortfall": any(item["amount"] > 0 for item in resource_missing),
            }
            city_groups.setdefault(city_name, []).append(step_display)

        # Compute remaining cost from steps still in the plan
        remaining_totals: dict[str, int] = {}
        for step in raw_steps:
            step_totals = step.get("totals") or {}
            for key in RESOURCE_ICON_MAP:
                lookup_key = "glas" if key == "crystal" else key
                remaining_totals[key] = remaining_totals.get(key, 0) + _to_int(step_totals.get(lookup_key), 0)

        spent_by_resource = []
        for key, (label, icon) in RESOURCE_ICON_MAP.items():
            lookup_key = "glas" if key == "crystal" else key
            total_amount = _to_int((summary.get("totals") or {}).get(lookup_key), 0)
            reserved_amount = _to_int((summary.get("reserved_local") or {}).get(lookup_key), 0)
            missing_amount = _to_int((summary.get("missing") or {}).get(lookup_key), 0)
            spent_amount = max(0, total_amount - remaining_totals.get(key, 0))
            if total_amount > 0:
                totals_by_resource.append({"key": key, "label": label, "icon": icon, "amount": total_amount})
            if reserved_amount > 0:
                reserved_by_resource.append({"key": key, "label": label, "icon": icon, "amount": reserved_amount})
            if missing_amount > 0:
                missing_by_resource.append({"key": key, "label": label, "icon": icon, "amount": missing_amount})
            if spent_amount > 0:
                spent_by_resource.append({"key": key, "label": label, "icon": icon, "amount": spent_amount, "remaining": remaining_totals.get(key, 0)})

        current_message = ""
        blocker_message = ""
        for message in reversed(logs):
            if not current_message and ("Evolucao iniciada:" in message or "Construcao iniciada:" in message):
                current_message = message.split(":", 1)[-1].strip()
            lowered = message.lower()
            if not blocker_message and (
                "aguardando" in lowered
                or "sem recurso" in lowered
                or "nao foi possivel" in lowered
                or "obra em andamento" in lowered
            ):
                blocker_message = message
            if current_message and blocker_message:
                break

        city_cards = []
        for city_name, city_steps in city_groups.items():
            # Mark the current active step based on the last build-started log
            current_step_name = ""
            if current_message:
                for step in city_steps:
                    if step["building_name"].lower() in current_message.lower():
                        step["is_current"] = True
                        current_step_name = step["building_name"]
                        break
            city_cards.append({
                "city_name": city_name,
                "step_count": len(city_steps),
                "shortfall_count": sum(1 for step in city_steps if step["has_shortfall"]),
                "current_step": current_step_name,
                # Open cities with an active build or shortfall by default
                "open_by_default": bool(current_step_name or any(s["has_shortfall"] for s in city_steps)),
                "steps": city_steps,
            })
        city_cards.sort(key=lambda item: item["city_name"].lower())

        return {
            "step_count": sum(item["step_count"] for item in city_cards),
            "city_count": len(city_cards),
            "queue_strategy_label": CONSTRUCTION_QUEUE_STRATEGY_META.get(str(inputs.get("queue_strategy") or "eta_first"), "Ordem do plano"),
            "auto_transport": cls._bool_label(inputs.get("auto_transport", True)),
            "city_cards": city_cards,
            "totals_by_resource": totals_by_resource,
            "reserved_by_resource": reserved_by_resource,
            "missing_by_resource": missing_by_resource,
            "spent_by_resource": spent_by_resource,
            "has_progress": bool(spent_by_resource),
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
        return super().get_queryset().select_related("account", "node", "profile")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        logs_qs = self.object.logs.all()
        child_jobs = list(Job.objects.filter(source_job_id=self.object.pk).order_by("-created_at"))
        context["logs"] = logs_qs
        context["log_rows"] = _build_log_rows(self.object, list(logs_qs))
        action_info = ACTION_CATALOG.get(self.object.action_code)
        context["action_name"] = action_info["name"] if action_info else None
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
        context["construction_plan"] = inputs.get("construction_plan_json") if isinstance(inputs.get("construction_plan_json"), list) else []
        context["construction_summary"] = inputs.get("construction_summary") if isinstance(inputs.get("construction_summary"), dict) else {}
        context["construction_reservations"] = self.object.construction_reservations.filter(status="active")
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


class JobCancelView(LoginRequiredMixin, View):
    def post(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        job.status = "cancelled"
        job.save(update_fields=["status"])
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

            immediate_job = Job.objects.create(
                account=locked.account,
                game_account=locked.game_account,
                node=locked.node,
                profile=locked.profile,
                action_code=locked.action_code,
                source_job_id=locked.pk,
                inputs_json=locked.inputs_json,
                timeout_sec=locked.timeout_sec,
                status="queued",
            )

            JobLog.objects.create(
                job=locked,
                level="info",
                message=f"Agendamento ignorado manualmente; novo job imediato criado: {immediate_job.pk}",
            )

            transaction.on_commit(lambda: dispatch_job(immediate_job, eta=None))

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

            immediate_job = Job.objects.create(
                account=locked.account,
                game_account=locked.game_account,
                node=locked.node,
                profile=locked.profile,
                action_code=locked.action_code,
                source_job_id=locked.pk,
                inputs_json=locked.inputs_json,
                timeout_sec=locked.timeout_sec,
                status="queued",
            )

            JobLog.objects.create(
                job=locked,
                level="info",
                message=f"Retry manual solicitado; novo job imediato criado: {immediate_job.pk}",
            )

            transaction.on_commit(lambda: dispatch_job(immediate_job, eta=None))

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
