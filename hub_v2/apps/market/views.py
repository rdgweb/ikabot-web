"""
Market views — full management dashboard.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, TemplateView

from apps.accounts.models import GameAccount
from apps.game.models import AccountSnapshot
from core.mixins.views import FilterSortListView, HtmxPartialMixin
from .filters import MarketOrderFilter
from .models import InternalMarketOrder
from . import services

logger = logging.getLogger(__name__)

RESOURCE_LABELS = {0: "Madeira", 1: "Vinho", 2: "Mármore", 3: "Cristal", 4: "Enxofre"}


def _build_participant_rows() -> list[dict]:
    """Return all GameAccounts with snapshot data for the participants table."""
    game_accounts = (
        GameAccount.objects.filter(active=True)
        .select_related("account", "account__node")
        .order_by("open_for_market", "account__node__name", "name")
    )
    # Prefetch snapshots in one query
    snaps = {
        s.game_account_id: s
        for s in AccountSnapshot.objects.filter(game_account__in=game_accounts)
    }

    rows = []
    for ga in game_accounts:
        snap = snaps.get(ga.pk)
        base = (snap.base_snapshot or {}) if snap else {}
        cities = []
        if snap:
            raw = snap.cities or {}
            if isinstance(raw, list):
                cities = [c for c in raw if isinstance(c, dict)]
            elif isinstance(raw, dict):
                cities = [c for c in raw.values() if isinstance(c, dict)]

        # Aggregate stock across cities
        stock = {"wood": 0, "wine": 0, "marble": 0, "crystal": 0, "sulfur": 0}
        for city in cities:
            stock["wood"] += int(city.get("wood") or 0)
            stock["wine"] += int(city.get("wine") or 0)
            stock["marble"] += int(city.get("marble") or 0)
            stock["crystal"] += int(city.get("crystal") or 0)
            stock["sulfur"] += int(city.get("sulfur") or 0)

        rows.append({
            "ga": ga,
            "gold": int(base.get("gold") or 0),
            "stock": stock,
            "has_snapshot": snap is not None,
            "snapshot_updated_at": snap.updated_at if snap else None,
        })
    return rows


class MarketDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "market/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.now().date()

        # Stats
        qs = InternalMarketOrder.objects.all()
        ctx["stats"] = {
            "today": qs.filter(created_at__date=today).count(),
            "active": qs.filter(status__in=["created", "matched", "jobs_created", "jobs_running"]).count(),
            "completed": qs.filter(status="completed").count(),
            "failed": qs.filter(status="failed").count(),
        }

        # Participants
        ctx["participant_rows"] = _build_participant_rows()

        # For create order form
        ctx["buyer_choices"] = list(
            GameAccount.objects.filter(active=True)
            .select_related("account")
            .order_by("account__label", "name")
            .values("id", "name", "account__label")
        )
        ctx["resource_choices"] = InternalMarketOrder.RESOURCE_CHOICES
        return ctx


class MarketParticipantsPartialView(LoginRequiredMixin, TemplateView):
    """HTMX partial: participants table only (used for refresh after toggle)."""
    template_name = "market/partials/participants_table.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["participant_rows"] = _build_participant_rows()
        return ctx


class MarketOrdersPartialView(FilterSortListView):
    """HTMX partial: paginated, filterable order table."""
    model = InternalMarketOrder
    filterset_class = MarketOrderFilter
    template_name = "market/partials/order_table.html"
    partial_template_name = "market/partials/order_table.html"
    paginate_by = 20
    ordering_fields = ["status", "resource_idx", "amount", "created_at"]
    default_ordering = "-created_at"
    queryset = InternalMarketOrder.objects.select_related(
        "buyer_account", "buyer_game_account",
        "seller_account", "seller_game_account",
        "buyer_node", "seller_node",
    )


class MarketOrderDetailView(LoginRequiredMixin, DetailView):
    model = InternalMarketOrder
    template_name = "market/order_detail.html"
    context_object_name = "order"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related(
                "buyer_account", "buyer_game_account",
                "seller_account", "seller_game_account",
                "buyer_node", "seller_node",
                "buy_job", "sell_job", "redistribution_job",
            )
        )


class MarketOrderCreateView(LoginRequiredMixin, View):
    """POST: create an InternalMarketOrder from the dashboard form."""

    def post(self, request):
        ga_id = request.POST.get("buyer_ga_id")
        resource_idx = request.POST.get("resource_idx")
        amount = request.POST.get("amount")
        unit_price = request.POST.get("unit_price", "12")

        try:
            buyer_ga = GameAccount.objects.get(pk=ga_id)
            resource_idx = int(resource_idx)
            amount = int(amount)
            unit_price = int(unit_price)
            if resource_idx not in range(5) or amount <= 0 or unit_price <= 0:
                raise ValueError("invalid params")
        except Exception as exc:
            trigger = json.dumps({"toast": {"type": "error", "message": f"Dados inválidos: {exc}"}})
            resp = HttpResponse(status=400)
            resp["HX-Trigger"] = trigger
            return resp

        try:
            order = services.create_internal_order(buyer_ga, resource_idx, amount, unit_price)
        except Exception as exc:
            logger.exception("Error creating market order")
            trigger = json.dumps({"toast": {"type": "error", "message": f"Erro ao criar ordem: {exc}"}})
            resp = HttpResponse(status=500)
            resp["HX-Trigger"] = trigger
            return resp

        if order is None:
            trigger = json.dumps({"toast": {"type": "warning", "message": "Nenhum vendedor elegível encontrado para esta ordem."}})
            resp = HttpResponse(status=200)
            resp["HX-Trigger"] = trigger
            return resp

        resource_label = RESOURCE_LABELS.get(resource_idx, str(resource_idx))
        trigger = json.dumps({
            "toast": {
                "type": "success",
                "message": f"Ordem criada: {amount}x {resource_label} @ {unit_price} ouro/un.",
            },
            "marketOrderCreated": True,
        })
        resp = HttpResponse(status=200)
        resp["HX-Trigger"] = trigger
        return resp


class MarketOrderCancelView(LoginRequiredMixin, View):
    """POST: cancel a pending InternalMarketOrder."""

    def post(self, request, pk):
        order = get_object_or_404(InternalMarketOrder, pk=pk)
        if order.status not in ("created", "matched", "jobs_created"):
            trigger = json.dumps({"toast": {"type": "error", "message": "Ordem não pode ser cancelada neste estado."}})
            resp = HttpResponse(status=400)
            resp["HX-Trigger"] = trigger
            return resp

        order.status = "canceled"
        order.save(update_fields=["status", "updated_at"])

        resource_label = RESOURCE_LABELS.get(order.resource_idx, str(order.resource_idx))
        trigger = json.dumps({
            "toast": {
                "type": "success",
                "message": f"Ordem de {order.amount}x {resource_label} cancelada.",
            },
            "marketOrderCanceled": True,
        })
        resp = HttpResponse(status=200)
        resp["HX-Trigger"] = trigger
        return resp
