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
from apps.market.models import BlackMarketOffer

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
                    "baseline_count": t.baseline_count,          # N-184: kept here, so a retry never trains again
                    "failure_reason": t.failure_reason,
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

        if mode == "liquidation" and not services.LIQUIDATION_AVAILABLE:
            # N-184: nothing lists the bank's units nor buys them back yet: a liquidation cycle would wait forever
            return Response({"error": "liquidation_not_available"}, status=status.HTTP_400_BAD_REQUEST)
        if mode == "liquidation":
            cycle = services.create_liquidation_cycle(config, manager_job=manager_job)
        else:
            try:
                cycle = services.create_accumulation_cycle(config, target_units, manager_job=manager_job)
            except ValueError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

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
        failure_reason = request.data.get("failure_reason")
        baseline_count = request.data.get("baseline_count")

        if task.status in ("sold", "cancelled") and new_status not in (None, "", task.status):
            # N-184: a task the bank already bought or that was cancelled by hand does not come back
            return Response({"ok": True, "task_status": task.status, "cycle_advanced": False, "ignored": True})

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
        if failure_reason:
            task.failure_reason = str(failure_reason)[:500]
            updates.append("failure_reason")
        if baseline_count is not None and task.baseline_count is None:
            task.baseline_count = max(0, int(baseline_count))     # written once: the stock before the cycle trained
            updates.append("baseline_count")

        task.save(update_fields=updates)

        # N-184: when no task is training any more the cycle goes on with what was listed, or fails with the reasons
        cycle = task.cycle
        outcome = services.settle_cycle(cycle)
        cycle.refresh_from_db()
        return Response({
            "ok": True,
            "task_status": task.status,
            "baseline_count": task.baseline_count,
            "cycle_advanced": outcome != "waiting",
            "cycle_status": cycle.status,
            "buy_job_id": str(cycle.buy_job_id) if cycle.buy_job_id else None,
        })


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
        # N-184: the offers the bank could not buy, with why: those tasks fail (the units stay listed in the game)
        for miss in request.data.get("failed") or []:
            task_id = str((miss or {}).get("task_id") or "")
            if task_id:
                GeneralsBankCycleTask.objects.filter(cycle=cycle, pk=task_id).exclude(status="sold").update(
                    status="failed", failure_reason=str(miss.get("reason") or "Banco não conseguiu comprar.")[:500],
                    updated_at=timezone.now())
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
                # Mark the task bought as sold (by its id when the agent sends it)
                bought = GeneralsBankCycleTask.objects.filter(cycle=cycle)
                if p.get("task_id"):
                    bought = bought.filter(pk=p.get("task_id"))
                else:
                    bought = bought.filter(producer_game_account=counterpart_ga, unit_id=int(p.get("unit_id", 0)))
                bought.update(status="sold", updated_at=timezone.now())
                offer_filters = {
                    "game_account": counterpart_ga,
                    "unit_id": int(p.get("unit_id", 0)),
                    "amount": int(p.get("quantity", 0)),
                    "unit_price": int(p.get("unit_price", 0)),
                    "status": "active",
                }
                seller_city_id = int(p.get("seller_city_id", 0) or 0)
                if seller_city_id > 0:
                    offer_filters["city_id"] = seller_city_id
                matching_offer = BlackMarketOffer.objects.filter(**offer_filters).order_by("-created_at").first()
                if matching_offer:
                    matching_offer.status = "sold"
                    matching_offer.closed_at = timezone.now()
                    matching_offer.save(update_fields=["status", "closed_at", "updated_at"])
            except Exception as exc:
                logger.warning("Failed to record purchase: %s", exc)

        services.advance_cycle_status(cycle, "sleeping", f"Banco comprou {len(purchases)} oferta(s).")
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

        # N-184: a cycle where part of what was listed was not bought is not a success
        tasks = list(cycle.tasks.all())
        missing = [t for t in tasks if t.status != "sold"]
        if tasks and missing:
            services.advance_cycle_status(
                cycle, "failed",
                f"Compra parcial: {len(tasks) - len(missing)} de {len(tasks)} oferta(s). As não compradas continuam "
                f"no Mercado Negro das produtoras.")
            return Response({"ok": True, "cycle_status": "failed"})
        services.advance_cycle_status(cycle, "completed", "Ciclo concluído: banco comprou tudo o que foi listado.")
        return Response({"ok": True, "cycle_status": "completed"})


class BankCycleFailView(APIView):
    """POST /api/agent/generals-bank/cycles/<cycle_id>/fail/"""
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request, cycle_id):
        try:
            cycle = GeneralsBankCycle.objects.get(pk=cycle_id)
        except GeneralsBankCycle.DoesNotExist:
            return Response({"error": "Cycle not found"}, status=status.HTTP_404_NOT_FOUND)

        note = str(request.data.get("note") or "Ciclo falhou.")
        services.advance_cycle_status(cycle, "failed", note)
        return Response({"ok": True, "cycle_status": cycle.status})


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
            "auto_cycle_enabled": config.auto_cycle_enabled,
            "auto_cycle_interval_minutes": config.auto_cycle_interval_minutes,
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
                    "production_template": p.production_template,
                    "sell_only_cycle_production": p.sell_only_cycle_production,
                    "keep_net_gold_positive": p.keep_net_gold_positive,
                }
                for p in config.producers.filter(is_active=True)
            ],
        })
