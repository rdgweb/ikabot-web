"""
Market views - full management dashboard.
"""

from __future__ import annotations

import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, TemplateView

from apps.accounts.models import GameAccount
from apps.game.models import AccountSnapshot
from core.mixins.views import FilterSortListView

from . import services
from .filters import MarketOrderFilter
from .models import InternalMarketOrder

logger = logging.getLogger(__name__)

RESOURCE_LABELS = {0: "Madeira", 1: "Vinho", 2: "Marmore", 3: "Cristal", 4: "Enxofre"}


def _snapshot_cities(game_account: GameAccount | None) -> list[dict]:
    if game_account is None:
        return []
    snap = AccountSnapshot.objects.filter(game_account=game_account).first()
    if snap is None:
        return []
    raw = snap.cities or {}
    if isinstance(raw, list):
        return [c for c in raw if isinstance(c, dict)]
    if isinstance(raw, dict):
        return [c for c in raw.values() if isinstance(c, dict)]
    return []


def _find_city_name(game_account: GameAccount | None, city_id: int | None) -> str:
    if game_account is None or city_id in (None, ""):
        return ""
    for city in _snapshot_cities(game_account):
        if str(city.get("id") or "") == str(city_id):
            return str(city.get("name") or city_id)
    return ""


def _market_city_options(game_account: GameAccount) -> list[dict]:
    options: list[dict] = []
    for city in _snapshot_cities(game_account):
        buildings = city.get("buildings") or city.get("position") or []
        branch_office_pos = None
        for item in buildings:
            if not isinstance(item, dict):
                continue
            if str(item.get("building") or "").strip() != "branchOffice":
                continue
            branch_office_pos = int(item.get("position") or 0)
            break
        if branch_office_pos is None:
            continue
        city_id = city.get("id")
        if city_id in (None, ""):
            continue
        options.append(
            {
                "city_id": int(city_id),
                "name": str(city.get("name") or city_id),
                "branch_office_pos": branch_office_pos,
                "wood": int(city.get("wood") or 0),
                "wine": int(city.get("wine") or 0),
                "marble": int(city.get("marble") or 0),
                "crystal": int(city.get("crystal") or 0),
                "sulfur": int(city.get("sulfur") or 0),
            }
        )
    options.sort(key=lambda item: item["name"].lower())
    return options


def _build_participant_rows() -> list[dict]:
    """Return all GameAccounts with snapshot data for the participants table."""
    game_accounts = (
        GameAccount.objects.filter(active=True)
        .select_related("account", "account__node")
        .order_by("-open_for_market", "account__node__name", "name")
    )
    snaps = {
        s.game_account_id: s
        for s in AccountSnapshot.objects.filter(game_account__in=game_accounts)
    }

    rows = []
    for ga in game_accounts:
        snap = snaps.get(ga.pk)
        base = (snap.base_snapshot or {}) if snap else {}
        cities = _snapshot_cities(ga)

        stock = {"wood": 0, "wine": 0, "marble": 0, "crystal": 0, "sulfur": 0}
        for city in cities:
            stock["wood"] += int(city.get("wood") or 0)
            stock["wine"] += int(city.get("wine") or 0)
            stock["marble"] += int(city.get("marble") or 0)
            stock["crystal"] += int(city.get("crystal") or 0)
            stock["sulfur"] += int(city.get("sulfur") or 0)

        rows.append(
            {
                "ga": ga,
                "gold": int(base.get("gold") or 0),
                "stock": stock,
                "has_snapshot": snap is not None,
                "snapshot_updated_at": snap.updated_at if snap else None,
            }
        )
    return rows


class MarketDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "market/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.now().date()

        qs = InternalMarketOrder.objects.all()
        ctx["stats"] = {
            "today": qs.filter(created_at__date=today).count(),
            "active": qs.filter(status__in=["created", "matched", "jobs_created", "jobs_running"]).count(),
            "completed": qs.filter(status="completed").count(),
            "failed": qs.filter(status="failed").count(),
        }
        ctx["participant_rows"] = _build_participant_rows()
        ctx["buyer_choices"] = [
            {
                "id": ga.id,
                "name": ga.name,
                "account__label": ga.account.label,
                "city_options": _market_city_options(ga),
            }
            for ga in (
                GameAccount.objects.filter(active=True)
                .select_related("account")
                .order_by("account__label", "name")
            )
        ]
        ctx["resource_choices"] = InternalMarketOrder.RESOURCE_CHOICES
        return ctx


class MarketParticipantsPartialView(LoginRequiredMixin, TemplateView):
    template_name = "market/partials/participants_table.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["participant_rows"] = _build_participant_rows()
        return ctx


class MarketOrdersPartialView(FilterSortListView):
    model = InternalMarketOrder
    filterset_class = MarketOrderFilter
    template_name = "market/partials/order_table.html"
    partial_template_name = "market/partials/order_table.html"
    paginate_by = 20
    ordering_fields = ["status", "resource_idx", "amount", "created_at"]
    default_ordering = "-created_at"
    queryset = InternalMarketOrder.objects.select_related(
        "buyer_account",
        "buyer_game_account",
        "seller_account",
        "seller_game_account",
        "buyer_node",
        "seller_node",
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
                "buyer_account",
                "buyer_game_account",
                "seller_account",
                "seller_game_account",
                "buyer_node",
                "seller_node",
                "buy_job",
                "sell_job",
                "redistribution_job",
            )
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        order = ctx["order"]
        ctx["buyer_city_name"] = _find_city_name(order.buyer_game_account, order.buyer_city_id)
        ctx["seller_city_name"] = _find_city_name(order.seller_game_account, order.seller_city_id)
        return ctx


class MarketOrderCreateView(LoginRequiredMixin, View):
    """POST: create an InternalMarketOrder from the dashboard form."""

    def post(self, request):
        ga_id = request.POST.get("buyer_ga_id")
        buyer_city_id = request.POST.get("buyer_city_id")
        resource_idx = request.POST.get("resource_idx")
        amount = request.POST.get("amount")
        unit_price = request.POST.get("unit_price", "0")

        try:
            buyer_ga = GameAccount.objects.get(pk=ga_id)
            buyer_city_id_value = int(buyer_city_id) if str(buyer_city_id or "").strip() else None
            resource_idx = int(resource_idx)
            amount = int(amount)
            unit_price = int(unit_price)
            if resource_idx not in range(5) or amount <= 0 or unit_price < 0:
                raise ValueError("invalid params")
        except Exception as exc:
            trigger = json.dumps({"toast": {"type": "error", "message": f"Dados invalidos: {exc}"}})
            resp = HttpResponse(status=400)
            resp["HX-Trigger"] = trigger
            return resp

        buyer_city_id_resolved, buyer_bo_pos = services.find_buyer_branchoffice(
            buyer_ga, preferred_city_id=buyer_city_id_value
        )
        if buyer_city_id_resolved is None:
            trigger = json.dumps({
                "toast": {
                    "type": "warning",
                    "message": f"A subconta {buyer_ga.name} nao tem Branch Office (Filial de Mercado) em nenhuma cidade. Construa o edificio primeiro.",
                }
            })
            resp = HttpResponse(status=200)
            resp["HX-Trigger"] = trigger
            return resp

        try:
            order = services.create_internal_order(
                buyer_ga,
                resource_idx,
                amount,
                unit_price,
                preferred_buyer_city_id=buyer_city_id_value,
            )
        except Exception as exc:
            logger.exception("Error creating market order")
            trigger = json.dumps({"toast": {"type": "error", "message": f"Erro ao criar ordem: {exc}"}})
            resp = HttpResponse(status=500)
            resp["HX-Trigger"] = trigger
            return resp

        if order is None:
            trigger = json.dumps(
                {"toast": {"type": "warning", "message": "Nenhum vendedor elegivel encontrado em outro no com estoque suficiente."}}
            )
            resp = HttpResponse(status=200)
            resp["HX-Trigger"] = trigger
            return resp

        resource_label = RESOURCE_LABELS.get(resource_idx, str(resource_idx))
        trigger = json.dumps(
            {
                "toast": {
                    "type": "success",
                    "message": f"Ordem criada: {amount}x {resource_label} @ {unit_price} ouro/un.",
                },
                "marketOrderCreated": True,
            }
        )
        resp = HttpResponse(status=200)
        resp["HX-Trigger"] = trigger
        return resp


class MarketOrderCancelView(LoginRequiredMixin, View):
    """POST: cancel a pending InternalMarketOrder."""

    def post(self, request, pk):
        order = get_object_or_404(InternalMarketOrder, pk=pk)
        if order.status not in ("created", "matched", "jobs_created"):
            trigger = json.dumps({"toast": {"type": "error", "message": "Ordem nao pode ser cancelada neste estado."}})
            resp = HttpResponse(status=400)
            resp["HX-Trigger"] = trigger
            return resp

        order.status = "canceled"
        order.save(update_fields=["status", "updated_at"])

        resource_label = RESOURCE_LABELS.get(order.resource_idx, str(order.resource_idx))
        trigger = json.dumps(
            {
                "toast": {
                    "type": "success",
                    "message": f"Ordem de {order.amount}x {resource_label} cancelada.",
                },
                "marketOrderCanceled": True,
            }
        )
        resp = HttpResponse(status=200)
        resp["HX-Trigger"] = trigger
        return resp
