"""
Views para o app Telegram — configuracao do bot, auditoria e partials HTMX.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import UpdateView

from apps.accounts.models import Account, GameAccount
from core.mixins.views import FilterSortListView

from .constants import EVENT_TYPES, EVENT_BY_FIELD
from .filters import MessageAuditFilter
from .forms import (
    TelegramBotConfigForm,
    TelegramGlobalNotificationForm,
    TelegramNotificationForm,
    NotificationTemplateForm,
)
from .models import TelegramBotConfig, TelegramAccountConfig, MessageAudit, NotificationTemplate
from .services.linking import (
    generate_link_code,
    generate_global_link_code,
    unlink_account,
    unlink_global,
)


# ── Global Config ───────────────────────────────────────────────────

class TelegramConfigView(LoginRequiredMixin, UpdateView):
    """
    Global Telegram configuration page.

    Shows bot token/enabled, global chat linking status, and
    global notification toggles.
    """

    model = TelegramBotConfig
    form_class = TelegramBotConfigForm
    template_name = "telegram/config.html"
    success_url = reverse_lazy("telegram:config")

    def get_object(self, queryset=None):
        obj, _created = TelegramBotConfig.objects.get_or_create(pk=1)
        return obj

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        obj = self.get_object()
        ctx["bot_config"] = obj
        ctx["notification_form"] = TelegramGlobalNotificationForm(instance=obj)

        # Enrich notification toggles with icons/descriptions
        enriched = []
        for field in ctx["notification_form"]:
            meta = EVENT_BY_FIELD.get(field.name)
            enriched.append({
                "field": field,
                "icon": meta["icon"] if meta else "",
                "description": meta["description"] if meta else "",
            })
        ctx["enriched_fields"] = enriched

        # Notification templates (seed if first time)
        NotificationTemplate.seed_defaults()
        templates = NotificationTemplate.objects.all()
        template_items = []
        for tpl in templates:
            meta = next((e for e in EVENT_TYPES if e["key"] == tpl.event_key), None)
            template_items.append({
                "template": tpl,
                "label": meta["label"] if meta else tpl.event_key,
                "description": meta["description"] if meta else "",
            })
        ctx["template_items"] = template_items

        return ctx


class TelegramSaveNotificationsView(LoginRequiredMixin, View):
    """POST: save global notification toggles (auto-save on toggle change)."""

    def post(self, request):
        config, _ = TelegramBotConfig.objects.get_or_create(pk=1)
        form = TelegramGlobalNotificationForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            config.refresh_from_db()

        # Re-render the notification card
        enriched = []
        new_form = TelegramGlobalNotificationForm(instance=config)
        for field in new_form:
            meta = EVENT_BY_FIELD.get(field.name)
            enriched.append({
                "field": field,
                "icon": meta["icon"] if meta else "",
                "description": meta["description"] if meta else "",
            })

        html = render_to_string(
            "telegram/partials/global_notifications.html",
            {"bot_config": config, "enriched_fields": enriched, "notification_form": new_form},
            request=request,
        )
        response = HttpResponse(html)
        response["HX-Trigger"] = "showToast"
        return response


# ── Global Linking ───────────────────────────────────────────────────

class GlobalStartLinkingView(LoginRequiredMixin, View):
    """POST: generate global link code."""

    def post(self, request):
        generate_global_link_code()
        # Reload to get the updated link_code
        bot_config = TelegramBotConfig.objects.get(pk=1)

        html = render_to_string(
            "telegram/partials/global_link_pending.html",
            {"bot_config": bot_config},
            request=request,
        )
        return HttpResponse(html)


class GlobalCheckLinkStatusView(LoginRequiredMixin, View):
    """GET: poll global link status (HTMX every 3s)."""

    def get(self, request):
        bot_config, _ = TelegramBotConfig.objects.get_or_create(pk=1)

        if bot_config.link_status == "linked":
            html = render_to_string(
                "telegram/partials/global_link_status.html",
                {"bot_config": bot_config},
                request=request,
            )
            return HttpResponse(html)

        # Still pending — reuse the pending template (has code + polling)
        html = render_to_string(
            "telegram/partials/global_link_pending.html",
            {"bot_config": bot_config},
            request=request,
        )
        return HttpResponse(html)


class GlobalUnlinkView(LoginRequiredMixin, View):
    """POST: unlink global Telegram chat."""

    def post(self, request):
        unlink_global()
        bot_config, _ = TelegramBotConfig.objects.get_or_create(pk=1)

        html = render_to_string(
            "telegram/partials/global_link_status.html",
            {"bot_config": bot_config},
            request=request,
        )
        return HttpResponse(html)


# ── Notification Templates ────────────────────────────────────────────

class TemplateEditView(LoginRequiredMixin, View):
    """GET: render edit form for a template. POST: save changes."""

    # Sample data for preview rendering
    SAMPLE_CONTEXT = {
        "action_name": "Check Status",
        "ga_name": "Cidade01",
        "server_id": "s61-br",
        "account_name": "player@email.com",
        "node_name": "node-01",
        "agent_name": "agent-1",
        "exit_code": "1",
        "job_id": "abc-123",
        "status": "error",
        "error": "Timeout ao conectar",
    }

    def get(self, request, pk):
        tpl = get_object_or_404(NotificationTemplate, pk=pk)
        meta = next((e for e in EVENT_TYPES if e["key"] == tpl.event_key), None)
        form = NotificationTemplateForm(instance=tpl)
        preview = tpl.render(**self.SAMPLE_CONTEXT)

        ctx = {
            "tpl": tpl,
            "form": form,
            "label": meta["label"] if meta else tpl.event_key,
            "preview": preview,
            "available_vars": NotificationTemplate.AVAILABLE_VARS,
        }
        html = render_to_string(
            "telegram/partials/template_edit.html", ctx, request=request,
        )
        return HttpResponse(html)

    def post(self, request, pk):
        tpl = get_object_or_404(NotificationTemplate, pk=pk)
        meta = next((e for e in EVENT_TYPES if e["key"] == tpl.event_key), None)
        form = NotificationTemplateForm(request.POST, instance=tpl)
        if form.is_valid():
            form.save()
            tpl.refresh_from_db()

        preview = tpl.render(**self.SAMPLE_CONTEXT)

        ctx = {
            "tpl": tpl,
            "form": form,
            "label": meta["label"] if meta else tpl.event_key,
            "preview": preview,
            "available_vars": NotificationTemplate.AVAILABLE_VARS,
            "saved": True,
        }
        html = render_to_string(
            "telegram/partials/template_edit.html", ctx, request=request,
        )
        response = HttpResponse(html)
        response["HX-Trigger"] = "showToast"
        return response


class TemplateRowView(LoginRequiredMixin, View):
    """GET: return collapsed row for a template (used by Cancel button)."""

    def get(self, request, pk):
        tpl = get_object_or_404(NotificationTemplate, pk=pk)
        meta = next((e for e in EVENT_TYPES if e["key"] == tpl.event_key), None)
        html = render_to_string(
            "telegram/partials/template_row.html",
            {"item": {
                "template": tpl,
                "label": meta["label"] if meta else tpl.event_key,
                "description": meta["description"] if meta else "",
            }},
            request=request,
        )
        return HttpResponse(html)


class TemplateResetView(LoginRequiredMixin, View):
    """POST: reset a template to its default."""

    def post(self, request, pk):
        tpl = get_object_or_404(NotificationTemplate, pk=pk)
        defaults = NotificationTemplate.DEFAULTS.get(tpl.event_key)
        if defaults:
            tpl.icon = defaults["icon"]
            tpl.title_template = defaults["title_template"]
            tpl.body_template = defaults["body_template"]
            tpl.save()

        meta = next((e for e in EVENT_TYPES if e["key"] == tpl.event_key), None)
        html = render_to_string(
            "telegram/partials/template_row.html",
            {"item": {
                "template": tpl,
                "label": meta["label"] if meta else tpl.event_key,
                "description": meta["description"] if meta else "",
            }},
            request=request,
        )
        response = HttpResponse(html)
        response["HX-Trigger"] = "showToast"
        return response


# ── Audit ───────────────────────────────────────────────────────────

class TelegramAuditView(FilterSortListView):
    model = MessageAudit
    filterset_class = MessageAuditFilter
    template_name = "telegram/audit_list.html"
    partial_template_name = "telegram/partials/audit_table.html"
    context_object_name = "object_list"
    paginate_by = 50
    ordering_fields = ["created_at", "status", "channel"]
    default_ordering = "-created_at"

    def get_queryset(self):
        return super().get_queryset().select_related("account", "game_account", "node")


# ── Account Telegram Overview (HTMX partial) ───────────────────────

class AccountTelegramOverviewView(LoginRequiredMixin, View):
    """
    HTMX partial: card de Telegram na sidebar do detalhe da Account.
    """

    template = "telegram/partials/account_overview.html"

    def get(self, request, pk):
        account = get_object_or_404(Account, pk=pk)
        bot_config, _ = TelegramBotConfig.objects.get_or_create(pk=1)

        game_accounts = list(account.game_accounts.all().order_by("server_id"))

        # Bulk-ensure TelegramAccountConfig exists for all game_accounts
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

        ctx = {
            "account": account,
            "bot_config": bot_config,
            "bot_active": bot_config.is_active,
            "global_linked": bot_config.is_linked,
            "ga_configs": ga_configs,
            "has_any_linked": any(c["is_linked"] for c in ga_configs) or bot_config.is_linked,
        }
        html = render_to_string(self.template, ctx, request=request)
        return HttpResponse(html)


# ── GameAccount Telegram Settings (HTMX partial) ───────────────────

class GameAccountTelegramSettingsView(LoginRequiredMixin, View):
    """
    HTMX partial: configuracao Telegram de uma subconta especifica.
    """

    template = "telegram/partials/ga_settings.html"

    def _get_context(self, ga):
        tg_config, _ = TelegramAccountConfig.objects.get_or_create(
            game_account=ga,
        )
        bot_config, _ = TelegramBotConfig.objects.get_or_create(pk=1)

        form = TelegramNotificationForm(instance=tg_config)

        enriched_fields = []
        for field in form:
            meta = EVENT_BY_FIELD.get(field.name)
            enriched_fields.append({
                "field": field,
                "icon": meta["icon"] if meta else "",
                "description": meta["description"] if meta else "",
                "is_toggle": field.name.startswith("notify_"),
            })

        return {
            "ga": ga,
            "tg_config": tg_config,
            "bot_config": bot_config,
            "form": form,
            "enriched_fields": enriched_fields,
            "is_linked": bool(tg_config.chat_id),
            "uses_global": not tg_config.chat_id and bool(bot_config.chat_id),
            "bot_active": bot_config.is_active,
            "global_linked": bot_config.is_linked,
        }

    def get(self, request, pk):
        ga = get_object_or_404(GameAccount, pk=pk)
        ctx = self._get_context(ga)
        html = render_to_string(self.template, ctx, request=request)
        return HttpResponse(html)

    def post(self, request, pk):
        ga = get_object_or_404(GameAccount, pk=pk)
        tg_config, _ = TelegramAccountConfig.objects.get_or_create(
            game_account=ga,
        )

        form = TelegramNotificationForm(request.POST, instance=tg_config)
        if form.is_valid():
            form.save()

        ctx = self._get_context(ga)
        html = render_to_string(self.template, ctx, request=request)
        response = HttpResponse(html)
        if form.is_valid():
            response["HX-Trigger"] = "showToast"
        return response


# ── Per-GA Linking flow (HTMX partials) ─────────────────────────────

class StartLinkingView(LoginRequiredMixin, View):
    """POST: gera codigo de vinculacao para uma subconta."""

    template = "telegram/partials/link_pending.html"

    def post(self, request, pk):
        ga = get_object_or_404(GameAccount, pk=pk)
        generate_link_code(ga.pk)
        # Reload to get the updated link_code
        tg_config = TelegramAccountConfig.objects.get(game_account=ga)
        bot_config, _ = TelegramBotConfig.objects.get_or_create(pk=1)

        ctx = {
            "ga": ga,
            "tg_config": tg_config,
            "bot_config": bot_config,
        }
        html = render_to_string(self.template, ctx, request=request)
        return HttpResponse(html)


class CheckLinkStatusView(LoginRequiredMixin, View):
    """GET: retorna o status atual da vinculacao por subconta."""

    def get(self, request, pk):
        ga = get_object_or_404(GameAccount, pk=pk)
        tg_config, _ = TelegramAccountConfig.objects.get_or_create(
            game_account=ga,
        )

        if tg_config.link_status == "linked":
            view = GameAccountTelegramSettingsView()
            view.request = request
            ctx = view._get_context(ga)
            html = render_to_string(
                "telegram/partials/ga_settings.html", ctx, request=request
            )
            response = HttpResponse(html)
            response["HX-Retarget"] = f"#tg-ga-inner-{ga.pk}"
            response["HX-Reswap"] = "outerHTML"
            return response

        html = render_to_string(
            "telegram/partials/link_status_polling.html",
            {"ga": ga, "tg_config": tg_config},
            request=request,
        )
        return HttpResponse(html)


class UnlinkView(LoginRequiredMixin, View):
    """POST: desvincula a subconta do Telegram."""

    def post(self, request, pk):
        ga = get_object_or_404(GameAccount, pk=pk)
        unlink_account(ga.pk)

        view = GameAccountTelegramSettingsView()
        view.request = request
        ctx = view._get_context(ga)
        html = render_to_string(
            "telegram/partials/ga_settings.html", ctx, request=request
        )
        return HttpResponse(html)
