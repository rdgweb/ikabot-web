"""Generals Bank UI views."""

from __future__ import annotations

import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView

from apps.accounts.models import GameAccount
from apps.game.models import AccountSnapshot
from apps.jobs.models import Job
from apps.jobs.services.workflows import create_job_with_workflow

from .models import GeneralsBankConfig, GeneralsBankCycle, GeneralsBankCycleTask, GeneralsBankProducer
from . import services

logger = logging.getLogger(__name__)


def _active_auto_scheduler_jobs(bank: GeneralsBankConfig):
    return (
        Job.objects.filter(
            game_account=bank.bank_game_account,
            action_code=806,
            status__in=["queued", "running", "scheduled"],
        )
        .filter(inputs_json__contains=f'"bank_config_id": "{bank.pk}"')
        .filter(inputs_json__contains='"_auto_loop": true')
        .order_by("-created_at")
    )


def _ensure_auto_scheduler(bank: GeneralsBankConfig, *, created_by=None) -> Job | None:
    existing = _active_auto_scheduler_jobs(bank).first()
    if existing:
        return existing
    ga = bank.bank_game_account
    return create_job_with_workflow(
        account=ga.account,
        game_account=ga,
        node=ga.account.node,
        action_code=806,
        inputs={
            "bank_config_id": str(bank.pk),
            "_auto_loop": True,
            "_poll_count": 0,
        },
        status="queued",
        created_by=created_by,
        trigger_type="generals_bank_auto",
    )


def _cancel_auto_scheduler(bank: GeneralsBankConfig) -> int:
    now = timezone.now()
    qs = _active_auto_scheduler_jobs(bank).filter(status__in=["queued", "scheduled"])
    return qs.update(status="cancelled", finished_at=now, updated_at=now, lease_expires_at=None)


def _normalize_units_map(raw: dict | None) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, value in (raw or {}).items():
        try:
            unit_id = str(int(key))
            qty = int(value)
        except Exception:
            continue
        if qty > 0:
            result[unit_id] = qty
    return result


def _parse_template_payload(raw_value: str | None) -> dict[str, int]:
    if not raw_value:
        return {}
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict):
        return {}
    return _normalize_units_map(parsed)


def _snapshot_cities(ga: GameAccount) -> list[dict]:
    snap = AccountSnapshot.objects.filter(game_account=ga).first()
    if snap is None:
        return []
    raw = snap.cities or {}
    if isinstance(raw, list):
        return [c for c in raw if isinstance(c, dict)]
    if isinstance(raw, dict):
        return [c for c in raw.values() if isinstance(c, dict)]
    return []


def _has_branch_office(city: dict) -> bool:
    for b in (city.get("buildings") or []):
        if isinstance(b, dict) and b.get("building") == "branchOffice":
            return True
    return False


def _has_black_market(city: dict) -> bool:
    for b in (city.get("buildings") or []):
        if isinstance(b, dict) and b.get("building") == "blackMarket":
            return True
    return False


class BankListView(LoginRequiredMixin, ListView):
    model = GeneralsBankConfig
    template_name = "generals_bank/list.html"
    context_object_name = "banks"

    def get_queryset(self):
        return (
            GeneralsBankConfig.objects
            .select_related("bank_game_account", "bank_game_account__account__node")
            .prefetch_related("producers")
            .order_by("-created_at")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        banks_ctx = []
        for bank in ctx["banks"]:
            gold = services.get_bank_gold(bank)
            active_cycle = services.get_active_cycle(bank)
            banks_ctx.append({
                "bank": bank,
                "gold": gold,
                "gold_low": gold < bank.min_gold_floor,
                "active_cycle": active_cycle,
                "producers_count": bank.producers.filter(is_active=True).count(),
            })
        ctx["banks_ctx"] = banks_ctx
        return ctx


class BankDetailView(LoginRequiredMixin, DetailView):
    model = GeneralsBankConfig
    template_name = "generals_bank/detail.html"
    context_object_name = "bank"

    def get_queryset(self):
        return GeneralsBankConfig.objects.select_related(
            "bank_game_account", "bank_game_account__account", "bank_game_account__account__node"
        ).prefetch_related("producers__producer_game_account")

    def get_context_data(self, **kwargs):
        from core.catalogs import TRAINING_UNITS
        _RESOURCE_CONFIG = [
            {"key": "wood",   "label": "Madeira", "icon": "game/resources/icon_wood.png"},
            {"key": "marble", "label": "Mármore", "icon": "game/resources/icon_marble.png"},
            {"key": "crystal","label": "Cristal",  "icon": "game/resources/icon_glass.png"},
            {"key": "wine",   "label": "Vinho",    "icon": "game/resources/icon_wine.png"},
            {"key": "sulfur", "label": "Enxofre",  "icon": "game/resources/icon_sulfur.png"},
        ]
        ctx = super().get_context_data(**kwargs)
        bank = self.object
        gold = services.get_bank_gold(bank)
        active_cycle = services.get_active_cycle(bank)

        # Bank account cities for buyer_city selection
        bank_cities = _snapshot_cities(bank.bank_game_account)
        buyer_city_options = [
            {"city_id": int(c.get("id", 0)), "name": str(c.get("name") or c.get("id", "?"))}
            for c in bank_cities
            if _has_branch_office(c)
        ]

        # All game accounts except bank for producer selection (different node)
        bank_node = bank.bank_game_account.account.node
        eligible_producers = (
            GameAccount.objects
            .filter(active=True)
            .exclude(account__node=bank_node)
            .exclude(pk=bank.bank_game_account_id)
            .select_related("account", "account__node")
            .order_by("name")
        )
        existing_producer_ids = set(bank.producers.values_list("producer_game_account_id", flat=True))

        ctx.update({
            "gold": gold,
            "gold_low": gold < bank.min_gold_floor,
            "active_cycle": active_cycle,
            "auto_scheduler_job": _active_auto_scheduler_jobs(bank).first(),
            "buyer_city_options": buyer_city_options,
            "eligible_producers": eligible_producers,
            "existing_producer_ids": existing_producer_ids,
            "producers": bank.producers.select_related("producer_game_account").filter(is_active=True),
            "fleet_units": TRAINING_UNITS.get("fleet", []),
            "troop_units": TRAINING_UNITS.get("troops", []),
            "all_units": TRAINING_UNITS.get("troops", []) + TRAINING_UNITS.get("fleet", []),
            "unit_catalog_json": json.dumps(TRAINING_UNITS.get("troops", []) + TRAINING_UNITS.get("fleet", [])),
            "resource_config": _RESOURCE_CONFIG,
            "producer_plan": services.build_producer_execution_plan(bank),
            "producer_plan_json": json.dumps(services.build_producer_execution_plan(bank)),
        })
        return ctx


class BankCreateView(LoginRequiredMixin, View):
    def get(self, request):
        from django.template.response import TemplateResponse
        bank_node_id = request.GET.get("node")
        eligible_accounts = (
            GameAccount.objects
            .filter(active=True)
            .exclude(generals_bank_config__isnull=False)
            .select_related("account", "account__node")
            .order_by("name")
        )
        return TemplateResponse(request, "generals_bank/create.html", {
            "eligible_accounts": eligible_accounts,
        })

    def post(self, request):
        bank_ga_id = request.POST.get("bank_game_account")
        buyer_city_id = request.POST.get("buyer_city_id") or None
        auto_vacation = request.POST.get("auto_vacation") == "on"
        min_gold_floor = int(request.POST.get("min_gold_floor") or 10000)
        notes = request.POST.get("notes", "")

        try:
            ga = GameAccount.objects.get(pk=bank_ga_id, active=True)
        except GameAccount.DoesNotExist:
            return HttpResponse("Conta não encontrada.", status=400)

        if hasattr(ga, "generals_bank_config"):
            return HttpResponse("Esta conta já tem um banco configurado.", status=400)

        config = GeneralsBankConfig.objects.create(
            bank_game_account=ga,
            buyer_city_id=int(buyer_city_id) if buyer_city_id else None,
            auto_vacation=auto_vacation,
            min_gold_floor=min_gold_floor,
            notes=notes,
        )
        return redirect("generals_bank:detail", pk=config.pk)


class BankUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        bank = get_object_or_404(GeneralsBankConfig, pk=pk)
        buyer_city_id = request.POST.get("buyer_city_id") or None
        bank.auto_vacation = request.POST.get("auto_vacation") == "on"
        bank.auto_cycle_enabled = request.POST.get("auto_cycle_enabled") == "on"
        bank.auto_cycle_interval_minutes = max(5, int(request.POST.get("auto_cycle_interval_minutes") or 30))
        bank.min_gold_floor = int(request.POST.get("min_gold_floor") or 10000)
        bank.notes = request.POST.get("notes", "")
        bank.buyer_city_id = int(buyer_city_id) if buyer_city_id else None
        bank.is_active = request.POST.get("is_active") == "on"
        bank.save()
        if bank.auto_cycle_enabled and bank.is_active:
            _ensure_auto_scheduler(bank, created_by=request.user)
        else:
            _cancel_auto_scheduler(bank)
        return redirect("generals_bank:detail", pk=bank.pk)


class BankProducerAddView(LoginRequiredMixin, View):
    def post(self, request, pk):
        bank = get_object_or_404(GeneralsBankConfig, pk=pk)
        producer_ga_id = request.POST.get("producer_game_account")
        min_pop = int(request.POST.get("min_population_reserve") or 0)
        production_template = _parse_template_payload(request.POST.get("production_template"))

        try:
            ga = GameAccount.objects.get(pk=producer_ga_id, active=True)
        except GameAccount.DoesNotExist:
            return HttpResponse("Conta não encontrada.", status=400)

        min_reserves = {}
        for res in ("wood", "marble", "crystal", "wine", "sulfur"):
            try:
                v = int(request.POST.get(f"res_{res}") or 0)
                if v > 0:
                    min_reserves[res] = v
            except (ValueError, TypeError):
                pass

        GeneralsBankProducer.objects.update_or_create(
            bank_config=bank,
            producer_game_account=ga,
            defaults={
                "min_resource_reserves": min_reserves,
                "min_population_reserve": min_pop,
                "production_template": production_template,
                "sell_only_cycle_production": request.POST.get("sell_only_cycle_production", "on") == "on",
                "keep_net_gold_positive": request.POST.get("keep_net_gold_positive", "on") == "on",
                "is_active": True,
            },
        )
        return redirect("generals_bank:detail", pk=bank.pk)


class BankProducerUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk, producer_pk):
        bank = get_object_or_404(GeneralsBankConfig, pk=pk)
        producer = get_object_or_404(GeneralsBankProducer, pk=producer_pk, bank_config=bank)
        min_pop = int(request.POST.get("min_population_reserve") or 0)
        production_template = _parse_template_payload(request.POST.get("production_template"))

        min_reserves = {}
        for res in ("wood", "marble", "crystal", "wine", "sulfur"):
            try:
                v = int(request.POST.get(f"res_{res}") or 0)
                if v > 0:
                    min_reserves[res] = v
            except (ValueError, TypeError):
                pass

        producer.min_resource_reserves = min_reserves
        producer.min_population_reserve = min_pop
        producer.production_template = production_template
        producer.sell_only_cycle_production = request.POST.get("sell_only_cycle_production") == "on"
        producer.keep_net_gold_positive = request.POST.get("keep_net_gold_positive") == "on"
        producer.is_active = request.POST.get("is_active") == "on"
        producer.save()
        return redirect("generals_bank:detail", pk=bank.pk)


class BankProducerRemoveView(LoginRequiredMixin, View):
    def post(self, request, pk, producer_pk):
        bank = get_object_or_404(GeneralsBankConfig, pk=pk)
        GeneralsBankProducer.objects.filter(pk=producer_pk, bank_config=bank).delete()
        return redirect("generals_bank:detail", pk=bank.pk)


class BankDeleteView(LoginRequiredMixin, View):
    """POST /generais/<pk>/excluir/ - cancela jobs auto e apaga o banco."""

    def post(self, request, pk):
        bank = get_object_or_404(GeneralsBankConfig, pk=pk)
        _cancel_auto_scheduler(bank)
        active_cycle = services.get_active_cycle(bank)
        if active_cycle:
            now = timezone.now()
            for job_field in ("manager_job", "buy_job"):
                job = getattr(active_cycle, job_field, None)
                if job and job.status in ("queued", "running", "scheduled"):
                    Job.objects.filter(pk=job.pk).update(
                        status="cancelled",
                        finished_at=now,
                        updated_at=now,
                        lease_expires_at=None,
                    )
            for task in GeneralsBankCycleTask.objects.filter(cycle=active_cycle):
                for job_field in ("training_job", "transport_job", "sell_job"):
                    job = getattr(task, job_field, None)
                    if job and job.status in ("queued", "running", "scheduled"):
                        Job.objects.filter(pk=job.pk).update(
                            status="cancelled",
                            finished_at=now,
                            updated_at=now,
                            lease_expires_at=None,
                        )
        bank.delete()
        return redirect("generals_bank:list")


class BankStartCycleView(LoginRequiredMixin, View):
    """POST — creates manager job (ac=806) to start a cycle."""

    def post(self, request, pk):
        bank = get_object_or_404(GeneralsBankConfig, pk=pk)

        active = services.get_active_cycle(bank)
        if active:
            return JsonResponse({"error": "Já existe um ciclo ativo.", "cycle_id": str(active.pk)}, status=409)

        simulate = request.POST.get("simulate") == "1"
        target_units = _parse_template_payload(request.POST.get("target_units"))
        plan = services.build_producer_execution_plan(bank)
        effective_target_units = target_units or plan.get("aggregate_units") or {}

        if simulate:
            return JsonResponse({
                "ok": True,
                "simulate": True,
                "aggregate_units": effective_target_units,
                "warnings": plan.get("warnings") or [],
                "tasks": plan.get("tasks") or [],
            })

        if not effective_target_units:
            return JsonResponse({"error": "Nenhuma composicao configurada nas produtoras."}, status=400)

        ga = bank.bank_game_account
        job = create_job_with_workflow(
            account=ga.account,
            game_account=ga,
            node=ga.account.node,
            action_code=806,
            inputs={
                "bank_config_id": str(bank.pk),
                "target_units": effective_target_units,
            },
            status="queued",
            created_by=request.user,
            trigger_type="user_start_cycle",
        )
        return JsonResponse({"ok": True, "job_id": str(job.pk)})


class BankCycleStatusPartialView(LoginRequiredMixin, View):
    """GET /generals-bank/<pk>/cycle-status/ — HTMX partial for live cycle status."""

    def get(self, request, pk):
        from django.template.response import TemplateResponse
        bank = get_object_or_404(GeneralsBankConfig, pk=pk)
        active_cycle = services.get_active_cycle(bank)
        tasks = list(active_cycle.tasks.select_related("producer_game_account").all()) if active_cycle else []
        return TemplateResponse(request, "generals_bank/partials/cycle_status.html", {
            "bank": bank,
            "active_cycle": active_cycle,
            "tasks": tasks,
        })


class BankHistoryPartialView(LoginRequiredMixin, View):
    """GET /generals-bank/<pk>/history/ — HTMX partial for cycle history."""

    def get(self, request, pk):
        from django.template.response import TemplateResponse
        bank = get_object_or_404(GeneralsBankConfig, pk=pk)
        cycles = bank.cycles.prefetch_related("transactions").order_by("-created_at")[:20]
        return TemplateResponse(request, "generals_bank/partials/history.html", {
            "bank": bank,
            "cycles": cycles,
        })
