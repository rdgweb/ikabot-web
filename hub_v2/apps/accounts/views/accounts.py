"""
Account CRUD views.
"""

import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import DetailView, CreateView, UpdateView, DeleteView

from core.mixins.views import FilterSortListView
from ..models import Account, GameAccount
from ..filters import AccountFilter
from ..forms import AccountCreateForm, AccountEditForm


class AccountListView(FilterSortListView):
    model = Account
    filterset_class = AccountFilter
    template_name = "accounts/account_list.html"
    partial_template_name = "accounts/partials/account_table.html"
    paginate_by = 25
    ordering_fields = ["label", "email", "node__name", "created_at"]
    default_ordering = "label"
    queryset = Account.objects.select_related("node")


class AccountDetailView(LoginRequiredMixin, DetailView):
    model = Account
    template_name = "accounts/account_detail.html"
    context_object_name = "account"

    def get_queryset(self):
        return (
            super().get_queryset()
            .select_related("node")
            .prefetch_related(
                "game_accounts",
                "game_accounts__telegram_config",
            )
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        account = self.object

        # Telegram context (rendered server-side, no HTMX load)
        from apps.telegram.models import TelegramBotConfig, TelegramAccountConfig

        bot_config, _ = TelegramBotConfig.objects.get_or_create(pk=1)

        # Bulk-ensure TelegramAccountConfig exists for all game_accounts
        # in a single query instead of N get_or_create calls.
        game_accounts = list(account.game_accounts.all().order_by("server_id"))
        existing_ga_ids = set(
            TelegramAccountConfig.objects
            .filter(game_account__in=game_accounts)
            .values_list("game_account_id", flat=True)
        )
        missing = [
            TelegramAccountConfig(game_account=ga)
            for ga in game_accounts
            if ga.pk not in existing_ga_ids
        ]
        if missing:
            TelegramAccountConfig.objects.bulk_create(missing, ignore_conflicts=True)

        # Single query to fetch all configs, keyed by game_account_id
        tg_map = {
            tg.game_account_id: tg
            for tg in TelegramAccountConfig.objects.filter(
                game_account__in=game_accounts
            )
        }

        ga_configs = []
        for ga in game_accounts:
            tg = tg_map.get(ga.pk)
            ga_configs.append({
                "ga": ga,
                "tg": tg,
                "is_linked": bool(tg and tg.chat_id),
                "uses_global": not (tg and tg.chat_id) and bool(bot_config.chat_id),
            })

        ctx["bot_config"] = bot_config
        ctx["bot_active"] = bot_config.is_active
        ctx["global_linked"] = bot_config.is_linked
        ctx["ga_configs"] = ga_configs
        ctx["has_any_linked"] = any(c["is_linked"] for c in ga_configs) or bot_config.is_linked
        return ctx


class AccountCreateView(LoginRequiredMixin, CreateView):
    model = Account
    form_class = AccountCreateForm
    template_name = "accounts/account_create.html"
    success_url = reverse_lazy("accounts:account-list")


class AccountEditView(LoginRequiredMixin, UpdateView):
    model = Account
    form_class = AccountEditForm
    template_name = "accounts/account_edit.html"

    def get_success_url(self):
        return reverse_lazy("accounts:account-detail", kwargs={"pk": self.object.pk})


class AccountDeleteView(LoginRequiredMixin, DeleteView):
    model = Account
    success_url = reverse_lazy("accounts:account-list")
    template_name = "accounts/account_confirm_delete.html"


class AccountToggleView(LoginRequiredMixin, View):
    """POST: toggle active status of a lobby account. Returns HTMX toast."""

    def post(self, request, pk):
        account = get_object_or_404(Account, pk=pk)
        account.active = not account.active
        account.save(update_fields=["active"])

        status_label = "ativada" if account.active else "desativada"
        trigger = json.dumps({
            "toast": {
                "type": "success",
                "message": f"Conta {account.label} {status_label}.",
            },
            "accountToggled": True,
        })
        resp = HttpResponse(status=204)
        resp["HX-Trigger"] = trigger
        return resp


class GameAccountToggleView(LoginRequiredMixin, View):
    """POST: toggle active status of a GameAccount. Returns HTMX toast."""

    def post(self, request, pk):
        ga = get_object_or_404(GameAccount, pk=pk)
        ga.active = not ga.active
        ga.save(update_fields=["active"])

        status_label = "ativada" if ga.active else "desativada"
        trigger = json.dumps({
            "toast": {
                "type": "success",
                "message": f"Subconta {ga.name or ga.server_id} {status_label}.",
            },
            "gameAccountToggled": True,
        })
        resp = HttpResponse(status=204)
        resp["HX-Trigger"] = trigger
        return resp


class GameAccountBuildTimeView(LoginRequiredMixin, View):
    """POST: update build_time_reduction for a GameAccount."""

    def post(self, request, pk):
        ga = get_object_or_404(GameAccount, pk=pk)
        try:
            value = int(request.POST.get("build_time_reduction", 0))
            value = max(0, min(100, value))
        except (ValueError, TypeError):
            value = 0
        ga.build_time_reduction = value
        ga.save(update_fields=["build_time_reduction"])
        trigger = json.dumps({
            "toast": {
                "type": "success",
                "message": f"Modificador de tempo de {ga.name or ga.server_id} atualizado para {value}%.",
            },
        })
        resp = HttpResponse(status=204)
        resp["HX-Trigger"] = trigger
        return resp


class GameAccountGovernmentTimeView(LoginRequiredMixin, View):
    """POST: update government_time_reduction for a GameAccount."""

    def post(self, request, pk):
        ga = get_object_or_404(GameAccount, pk=pk)
        try:
            value = int(request.POST.get("government_time_reduction", 0))
            value = max(0, min(100, value))
        except (ValueError, TypeError):
            value = 0
        ga.government_time_reduction = value
        ga.save(update_fields=["government_time_reduction"])
        trigger = json.dumps({
            "toast": {
                "type": "success",
                "message": f"Redução de governo de {ga.name or ga.server_id} atualizada para {value}%.",
            },
        })
        resp = HttpResponse(status=204)
        resp["HX-Trigger"] = trigger
        return resp


class GameAccountMarketToggleView(LoginRequiredMixin, View):
    """POST: toggle open_for_market for a GameAccount."""

    def post(self, request, pk):
        ga = get_object_or_404(GameAccount, pk=pk)
        ga.open_for_market = not ga.open_for_market
        ga.save(update_fields=["open_for_market"])
        state = "aberta" if ga.open_for_market else "fechada"
        trigger = json.dumps({
            "toast": {
                "type": "success",
                "message": f"{ga.name or ga.server_id}: conta {state} para o mercado.",
            },
            "marketParticipantsChanged": True,
        })
        resp = HttpResponse(status=204)
        resp["HX-Trigger"] = trigger
        return resp


class GameAccountMarketStockView(LoginRequiredMixin, View):
    """POST: update market_min_stock for a GameAccount."""

    def post(self, request, pk):
        ga = get_object_or_404(GameAccount, pk=pk)
        try:
            value = max(0, int(request.POST.get("market_min_stock", 0)))
        except (ValueError, TypeError):
            value = 0
        ga.market_min_stock = value
        ga.save(update_fields=["market_min_stock"])
        trigger = json.dumps({
            "toast": {
                "type": "success",
                "message": f"Reserva de estoque de {ga.name or ga.server_id} atualizada para {value:,} unidades.",
            },
        })
        resp = HttpResponse(status=204)
        resp["HX-Trigger"] = trigger
        return resp


class GameAccountMarketGoldView(LoginRequiredMixin, View):
    """POST: update market_min_gold for a GameAccount."""

    def post(self, request, pk):
        ga = get_object_or_404(GameAccount, pk=pk)
        try:
            value = max(0, int(request.POST.get("market_min_gold", 0)))
        except (ValueError, TypeError):
            value = 0
        ga.market_min_gold = value
        ga.save(update_fields=["market_min_gold"])
        trigger = json.dumps({
            "toast": {
                "type": "success",
                "message": f"Reserva de ouro de {ga.name or ga.server_id} atualizada para {value:,} moedas.",
            },
        })
        resp = HttpResponse(status=204)
        resp["HX-Trigger"] = trigger
        return resp

