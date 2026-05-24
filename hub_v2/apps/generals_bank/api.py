"""Agent API for Generals Bank."""

from __future__ import annotations

import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import GameAccount
from apps.jobs.models import Job
from core.auth.backends import AgentTokenAuthentication
from core.auth.permissions import IsAgent

from .models import GeneralsBankConfig, GeneralsBankCycle, GeneralsBankCycleTask, GeneralsBankTransaction
from . import services

logger = logging.getLogger(__name__)


class BankCycleStatusView(APIView):
    """GET /api/agent/generals-bank/cycles/<cycle_id>/status/

    Runner 806 polls this to check progress.
    Returns cycle status + per-task breakdown.
    """
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def get(self, request, cycle_id):
        try:
            cycle = GeneralsBankCycle.objects.select_related("bank_config").get(pk=cycle_id)
        except GeneralsBankCycle.DoesNotExist:
            return Response({"error": "Cycle not found"}, status=status.HTTP_404_NOT_FOUND)

        tasks = list(cycle.tasks.select_related("producer_game_account").all())
        return Response({
            "ok": True,
            "cycle_id": str(cycle.pk),
            "mode": cycle.mode,
            "status": cycle.status,
            "is_terminal": cycle.is_terminal,
            "tasks": [
                {
                    "task_id": str(t.pk),
                    "producer": t.producer_game_account.name,
                    "city_id": t.city_id,
                    "city_name": t.city_name,
                    "unit_id": t.unit_id,
                    "unit_name": t.unit_name,
                    "quantity_target": t.quantity_target,
                    "quantity_done": t.quantity_done,
                    "status": t.status,
                    "bm_city_id": t.bm_city_id,
                    "bm_city_name": t.bm_city_name,
                    "unit_price": t.unit_price,
                    "sell_job_id": str(t.sell_job_id) if t.sell_job_id else None,
                }
                for t in tasks
            ],
        })


class BankCycleCreateView(APIView):
    """POST /api/agent/generals-bank/cycles/create/

    Runner 806 calls this to initialize a cycle with tasks.
    Hub creates training jobs on each producer.
    """
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request):
        bank_config_id = request.data.get("bank_config_id")
        target_units = request.data.get("target_units") or {}
        manager_job_id = request.data.get("manager_job_id")
        mode = request.data.get("mode", "accumulation")

        if not bank_config_id:
            return Response({"error": "bank_config_id required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            config = GeneralsBankConfig.objects.select_related(
                "bank_game_account", "bank_game_account__account", "bank_game_account__account__node"
            ).get(pk=bank_config_id, is_active=True)
        except GeneralsBankConfig.DoesNotExist:
            return Response({"error": "BankConfig not found"}, status=status.HTTP_404_NOT_FOUND)

        existing = services.get_active_cycle(config)
        if existing:
            return Response(
                {"error": "active_cycle_exists", "cycle_id": str(existing.pk)},
                status=status.HTTP_409_CONFLICT,
            )

        manager_job = Job.objects.filter(pk=manager_job_id).first() if manager_job_id else None

        if mode == "liquidation":
            cycle = services.create_liquidation_cycle(config, manager_job=manager_job)
        else:
            if not target_units:
                return Response({"error": "target_units required for accumulation"}, status=status.HTTP_400_BAD_REQUEST)
            cycle = services.create_accumulation_cycle(config, target_units, manager_job=manager_job)

        tasks = list(cycle.tasks.select_related("producer_game_account").all())
        return Response({
            "ok": True,
            "cycle_id": str(cycle.pk),
            "mode": cycle.mode,
            "status": cycle.status,
            "tasks": [
                {
                    "task_id": str(t.pk),
                    "producer_ga_id": str(t.producer_game_account_id),
                    "producer_name": t.producer_game_account.name,
                    "city_id": t.city_id,
                    "city_name": t.city_name,
                    "unit_id": t.unit_id,
                    "quantity_target": t.quantity_target,
                    "bm_city_id": t.bm_city_id,
                }
                for t in tasks
            ],
        }, status=status.HTTP_201_CREATED)


class BankTaskUpdateView(APIView):
    """POST /api/agent/generals-bank/tasks/<task_id>/update/

    Producer runners call this to report task progress.
    """
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request, task_id):
        try:
            task = GeneralsBankCycleTask.objects.select_related("cycle", "cycle__bank_config").get(pk=task_id)
        except GeneralsBankCycleTask.DoesNotExist:
            return Response({"error": "Task not found"}, status=status.HTTP_404_NOT_FOUND)

        new_status = request.data.get("status")
        quantity_done = request.data.get("quantity_done")
        unit_price = request.data.get("unit_price")
        unit_name = request.data.get("unit_name")
        sell_job_id = request.data.get("sell_job_id")

        updates = ["updated_at"]
        if new_status and new_status in dict(GeneralsBankCycleTask.STATUS_CHOICES):
            task.status = new_status
            updates.append("status")
        if quantity_done is not None:
            task.quantity_done = int(quantity_done)
            updates.append("quantity_done")
        if unit_price is not None:
            task.unit_price = int(unit_price)
            updates.append("unit_price")
        if unit_name:
            task.unit_name = str(unit_name)
            updates.append("unit_name")
        if sell_job_id:
            job = Job.objects.filter(pk=sell_job_id).first()
            if job:
                task.sell_job = job
                updates.append("sell_job")

        task.save(update_fields=updates)

        # Check if all tasks are listed → advance cycle to bank_buying
        cycle = task.cycle
        if new_status == "listed" and services.all_tasks_listed(cycle):
            services.advance_cycle_status(cycle, "bank_buying", "Todos produtores listaram. Criando job de compra do banco.")
            buy_job = services.create_bank_buy_job(cycle)
            return Response({
                "ok": True,
                "task_status": task.status,
                "cycle_advanced": True,
                "cycle_status": cycle.status,
                "buy_job_id": str(buy_job.pk) if buy_job else None,
            })

        return Response({"ok": True, "task_status": task.status, "cycle_advanced": False})


class BankBuyCompleteView(APIView):
    """POST /api/agent/generals-bank/cycles/<cycle_id>/buy-complete/

    Runner 807 calls this after buying from all producers.
    Hub records transactions and advances cycle status.
    """
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request, cycle_id):
        try:
            cycle = GeneralsBankCycle.objects.select_related(
                "bank_config", "bank_config__bank_game_account"
            ).get(pk=cycle_id)
        except GeneralsBankCycle.DoesNotExist:
            return Response({"error": "Cycle not found"}, status=status.HTTP_404_NOT_FOUND)

        purchases = request.data.get("purchases") or []
        for p in purchases:
            try:
                counterpart_ga = GameAccount.objects.filter(pk=p.get("producer_ga_id")).first()
                job = Job.objects.filter(pk=p.get("job_id")).first() if p.get("job_id") else None
                services.record_buy_transaction(
                    cycle=cycle,
                    unit_id=int(p.get("unit_id", 0)),
                    unit_name=str(p.get("unit_name", "")),
                    quantity=int(p.get("quantity", 0)),
                    unit_price=int(p.get("unit_price", 0)),
                    counterpart_ga=counterpart_ga,
                    job=job,
                )
                # Mark tasks as sold
                GeneralsBankCycleTask.objects.filter(
                    cycle=cycle,
                    producer_game_account=counterpart_ga,
                    unit_id=int(p.get("unit_id", 0)),
                ).update(status="sold", updated_at=timezone.now())
            except Exception as exc:
                logger.warning("Failed to record purchase: %s", exc)

        services.advance_cycle_status(cycle, "sleeping", "Banco comprou todas as unidades disponíveis.")
        return Response({"ok": True, "cycle_status": cycle.status})


class BankCycleCompleteView(APIView):
    """POST /api/agent/generals-bank/cycles/<cycle_id>/complete/

    Runner 807 calls this after the bank enters vacation mode.
    """
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request, cycle_id):
        try:
            cycle = GeneralsBankCycle.objects.get(pk=cycle_id)
        except GeneralsBankCycle.DoesNotExist:
            return Response({"error": "Cycle not found"}, status=status.HTTP_404_NOT_FOUND)

        services.advance_cycle_status(cycle, "completed", "Ciclo concluído com sucesso.")
        return Response({"ok": True})


class BankConfigView(APIView):
    """GET /api/agent/generals-bank/configs/<config_id>/

    Runner 806 fetches bank config to decide mode.
    """
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def get(self, request, config_id):
        try:
            config = GeneralsBankConfig.objects.select_related(
                "bank_game_account"
            ).prefetch_related("producers__producer_game_account").get(pk=config_id, is_active=True)
        except GeneralsBankConfig.DoesNotExist:
            return Response({"error": "BankConfig not found"}, status=status.HTTP_404_NOT_FOUND)

        gold = services.get_bank_gold(config)
        mode = services.determine_cycle_mode(config)
        active_cycle = services.get_active_cycle(config)

        return Response({
            "ok": True,
            "config_id": str(config.pk),
            "bank_ga_id": str(config.bank_game_account_id),
            "buyer_city_id": config.buyer_city_id,
            "auto_vacation": config.auto_vacation,
            "min_gold_floor": config.min_gold_floor,
            "current_gold": gold,
            "recommended_mode": mode,
            "active_cycle_id": str(active_cycle.pk) if active_cycle else None,
            "producers": [
                {
                    "producer_ga_id": str(p.producer_game_account_id),
                    "producer_name": p.producer_game_account.name,
                    "min_resource_reserves": p.min_resource_reserves,
                    "min_population_reserve": p.min_population_reserve,
                }
                for p in config.producers.filter(is_active=True)
            ],
        })
