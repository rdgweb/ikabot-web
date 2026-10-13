"""
Views para o app Telegram — configuracao do bot, auditoria e partials HTMX.
"""

import base64
import json
import re
from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.http import HttpResponse
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import UpdateView

from apps.accounts.models import Account, GameAccount
from core.mixins.views import FilterSortListView

from .constants import EVENT_TYPES, EVENT_BY_FIELD, NOT_EMITTED
from .filters import MessageAuditFilter
from .forms import (
    TelegramBotConfigForm,
    TelegramGlobalNotificationForm,
    TelegramNotificationForm,
    NotificationTemplateForm,
    TelegramIncomingCommandForm,
)
from .models import (
    TelegramBotConfig,
    TelegramAccountConfig,
    MessageAudit,
    NotificationTemplate,
    TelegramIncomingCommand,
)
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
        counts = sent_counts()
        items = [template_item(tpl, counts) for tpl in NotificationTemplate.objects.filter(game_account__isnull=True)]
        items.sort(key=lambda item: (-item["sent"], item["label"]))
        ctx["template_items"] = items
        # N-109: every notice that is sent has its whole text written from variables; the rest
        # are notices nothing sends yet (a toggle and a text, waiting for whoever emits them)
        ctx["template_groups"] = [
            {"title": "Avisos", "items": [item for item in items if item["emitted"]]},
            {"title": "Ainda sem envio", "items": [item for item in items if not item["emitted"]]},
        ]
        ctx["sent_total"] = sum(counts.values())

        TelegramIncomingCommand.seed_defaults()
        ctx["command_items"] = [
            {
                "command": command,
                "form": TelegramIncomingCommandForm(instance=command),
                "label": command.get_key_display(),
            }
            for command in TelegramIncomingCommand.objects.all()
        ]

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

COUNT_DAYS = 14

# what a notice looks like when none of its kind was sent yet (the editor prefers the last real one)
SAMPLE_CONTEXTS = {
    "cinema_available": {
        "count": 3, "accounts": "• BlackShadow701 — Midgard\n• HAVIT — lll1lll\n• ScorpiO — ScorpiO M",
        "reset_time": "00:00", "reset_in": "1h",
    },
    "island_monitor": {
        "ga_name": "BlackShadow701", "server_id": "s78-br", "title": "Mudança na ilha [83:90]",
        "island": "Ilha [83:90] — Colosso", "coords": "[83:90]", "resource": "Vinho", "miracle": "Colosso",
        "changes": "➕ Fulano [ABC] colonizou Nova Polis\n🔄 Velha: Ciclano → Beltrano", "changes_count": 2,
    },
    "attack_alert": {
        "ga_name": "BlackShadow701", "server_id": "s78-br", "mission": "Roubar", "target_city": "Valhala",
        "origin_player": "Fulano", "origin_city": "Polis Inimiga", "troops": 70, "fleet": 12, "time_left_human": "21min 22s",
    },
    "job_failed": {
        "ga_name": "BlackShadow701", "server_id": "s78-br", "action_name": "Distribuir Recursos", "city_name": "Valhala",
        "node_name": "local-dev", "exit_code": "1", "job_id": "71cfa2a2", "body": "Timeout ao conectar no servidor",
    },
    "job_done": {
        "ga_name": "BlackShadow701", "server_id": "s78-br", "action_name": "Enviar Recursos", "city_name": "Valhala",
        "node_name": "local-dev", "job_id": "71cfa2a2", "body": "",
    },
    # used only while the hub keeps no combat report (the preview prefers a real one)
    "combat_report": {
        "ga_name": "BlackShadow701", "server_id": "s78-br", "phase": "final", "headline": "Vitória em 2 rounds",
        "city": "Quartzum [80:71]", "versus": "BlackShadow701 × Godeus", "attacker": "BlackShadow701", "defender": "Godeus",
        "result": "Vitória", "round": 2, "total_rounds": 2, "attacker_losses": "sem perdas",
        "defender_losses": "Lanceiro -286, Muralha -7", "losses": "Atacante: sem perdas\nDefensor: Lanceiro -286, Muralha -7",
        "loot_line": "Saque: 80.631 madeira · 2.220 enxofre", "date": "03.06.2026 12:54:33",
    },
    "proxy_change": {
        "changes": "Conta Principal: 2 proxy(s) fora de uso, 2 novo(s) reservado(s).\nBloqueio de login liberado: BlackShadow701.",
        "provider": "Lista do provedor: 3 novo(s), 2 retirado(s).", "tests": "Teste: 18 de 20 proxies ok.",
        "reason_line": "Motivo da verificação: falha de proxy no login de BlackShadow701.",
    },
    "diplomacy_message": {
        "ga_name": "BlackShadow701", "server_id": "s78-br", "sender": "Fulano", "subject": "Tratado cultural",
        "game_date": "12.10.2026 18:00", "message_body": "Olá, aceita um tratado cultural?",
        "reply_command": "/responder 1234 <texto>", "accept_command": "/responder 1234 yes", "decline_command": "/responder 1234 no",
    },
}
GENERIC_SAMPLE = {
    "action_name": "Check Status", "ga_name": "Cidade01", "server_id": "s61-br", "account_name": "player@email.com",
    "node_name": "node-01", "agent_name": "agent-1", "exit_code": "1", "job_id": "abc-123", "status": "error",
    "error": "Timeout ao conectar", "title": "Aviso", "body": "Texto do aviso",
}


def event_meta(event_key: str) -> dict:
    return next((e for e in EVENT_TYPES if e["key"] == event_key), None) or {"label": event_key, "description": ""}


def sent_counts() -> dict:
    """{event: messages sent in the last COUNT_DAYS days}."""
    since = timezone.now() - timedelta(days=COUNT_DAYS)
    rows = MessageAudit.objects.filter(created_at__gte=since, status="sent").values("channel").annotate(n=Count("id"))
    return {row["channel"]: row["n"] for row in rows}


def template_item(tpl, counts: dict | None = None) -> dict:
    meta = event_meta(tpl.event_key)
    global_pk = tpl.pk
    if tpl.game_account_id:
        base = NotificationTemplate.for_event(tpl.event_key)
        global_pk = base.pk if base else tpl.pk
    return {
        "template": tpl,
        "global_pk": global_pk,          # the row of the list an account text lives under
        "label": meta["label"],
        "description": meta["description"],
        "structured": tpl.event_key in NotificationTemplate.EVENT_VARS,
        "emitted": tpl.event_key not in NOT_EMITTED,
        "sent": (counts or {}).get(tpl.event_key, 0),
        "account_name": (tpl.game_account.name or tpl.game_account.server_id) if tpl.game_account_id else "",
        "overrides": NotificationTemplate.objects.filter(event_key=tpl.event_key, game_account__isnull=False).count()
        if not tpl.game_account_id else 0,
    }


def preview_context(tpl) -> tuple[dict, object]:
    """(what the last real notice of this kind carried, when it was sent) or (a made-up one, None)."""
    sent = MessageAudit.objects.filter(channel=tpl.event_key, status="sent").exclude(context_json__in=("", "{}"))
    last = (sent.filter(game_account_id=tpl.game_account_id).order_by("-created_at").first() if tpl.game_account_id else None) \
        or sent.order_by("-created_at").first()
    if last is not None:
        try:
            context = json.loads(last.context_json or "{}")
        except ValueError:
            context = {}
        if isinstance(context, dict) and context:
            context = _complete(tpl, context)
            # a notice sent before the text had these variables would preview with holes in it
            own = {name.strip("{}") for name, _label in NotificationTemplate.EVENT_VARS.get(tpl.event_key, [])}
            used = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", f"{tpl.title_template}\n{tpl.body_template}"))
            if not (used & own) - set(context):
                return context, last.created_at
    if tpl.event_key == "combat_report":
        # no combat notice was sent yet: the latest combat the hub keeps is a truer sample than a made-up one
        from .services.combat import sample_combat

        stored, _account = sample_combat()
        if stored:
            return _complete(tpl, stored), None
    return _complete(tpl, {**GENERIC_SAMPLE, **SAMPLE_CONTEXTS.get(tpl.event_key, {})}), None


def _complete(tpl, context: dict) -> dict:
    if tpl.event_key == "island_monitor":
        from .services.digests import island_context

        return island_context(context)
    if tpl.event_key == "combat_report":
        from .services.combat import combat_context

        return combat_context(context)
    return context


def render_preview(tpl) -> tuple[str, object]:
    context, sent_at = preview_context(tpl)
    return tpl.render(**context), sent_at


def preview_picture(tpl) -> bytes | None:
    """The picture that goes with the notice in the preview and in the test (the combat has one)."""
    if tpl.event_key != "combat_report":
        return None
    from apps.combat.models import CombatReport
    from .services.combat import combat_picture

    context, _sent_at = preview_context(tpl)
    report = CombatReport.objects.filter(combat_id=context.get("combat_id") or 0).select_related("game_account").first()
    return combat_picture(context, report.game_account if report else None)


def editor_response(request, tpl, *, form=None, saved=False, notice="", notice_ok=True):
    preview, preview_at = render_preview(tpl)
    picture = preview_picture(tpl)
    taken = NotificationTemplate.objects.filter(event_key=tpl.event_key, game_account__isnull=False)
    html = render_to_string("telegram/partials/template_edit.html", {
        "tpl": tpl,
        "item": template_item(tpl),
        "form": form or NotificationTemplateForm(instance=tpl),
        "preview": preview,
        "preview_at": preview_at,
        "preview_image": base64.b64encode(picture).decode() if picture else "",
        "available_vars": NotificationTemplate.variables_for(tpl.event_key),
        "unknown_vars": tpl.unknown_variables(),
        "account_templates": list(taken.select_related("game_account")) if not tpl.game_account_id else [],
        "free_accounts": list(
            GameAccount.objects.filter(active=True, account__active=True)
            .exclude(pk__in=taken.values_list("game_account_id", flat=True)).order_by("name")
        ) if not tpl.game_account_id else [],
        "saved": saved,
        "notice": notice,
        "notice_ok": notice_ok,
    }, request=request)
    response = HttpResponse(html)
    if saved:
        response["HX-Trigger"] = "showToast"
    return response


def row_response(request, tpl, *, toast=False):
    html = render_to_string("telegram/partials/template_row.html", {"item": template_item(tpl, sent_counts())}, request=request)
    response = HttpResponse(html)
    if toast:
        response["HX-Trigger"] = "showToast"
    return response


class TemplateEditView(LoginRequiredMixin, View):
    """GET: the editor of a notice text. POST: save it."""

    def get(self, request, pk):
        return editor_response(request, get_object_or_404(NotificationTemplate, pk=pk))

    def post(self, request, pk):
        tpl = get_object_or_404(NotificationTemplate, pk=pk)
        form = NotificationTemplateForm(request.POST, instance=tpl)
        if form.is_valid():
            form.save()
            tpl.refresh_from_db()
            return editor_response(request, tpl, saved=True)
        return editor_response(request, tpl, form=form)


class TemplateRowView(LoginRequiredMixin, View):
    """GET: return collapsed row for a template (used by Cancel button)."""

    def get(self, request, pk):
        return row_response(request, get_object_or_404(NotificationTemplate, pk=pk))


class TemplateResetView(LoginRequiredMixin, View):
    """POST: reset a template to its default."""

    def post(self, request, pk):
        tpl = get_object_or_404(NotificationTemplate, pk=pk)
        tpl.reset_to_default()
        return editor_response(request, tpl, saved=True, notice="Texto padrão restaurado.")


class TemplateTestView(LoginRequiredMixin, View):
    """POST: send the notice as it is saved now to the global chat, filled like the preview (N-104)."""

    def post(self, request, pk):
        from .services.notifications import _send

        tpl = get_object_or_404(NotificationTemplate, pk=pk)
        bot = TelegramBotConfig.objects.filter(pk=1).first()
        if bot is None or not bot.is_active or not bot.chat_id:
            return editor_response(request, tpl, notice="Não há bot ativo com chat global vinculado para receber o teste.", notice_ok=False)
        text, _sent_at = render_preview(tpl)
        result = _send(bot.chat_id, "🧪 <i>Teste do modelo</i>\n" + text, preview_picture(tpl), None)
        if result.get("ok"):
            return editor_response(request, tpl, notice="Teste enviado para o chat global.")
        return editor_response(request, tpl, notice=f"O Telegram recusou o teste: {result.get('description', 'erro desconhecido')}", notice_ok=False)


class AccountTemplateCreateView(LoginRequiredMixin, View):
    """POST: give an account a text of its own for a notice, starting from the global one (N-104)."""

    def post(self, request, pk):
        base = get_object_or_404(NotificationTemplate, pk=pk, game_account__isnull=True)
        game_account = GameAccount.objects.filter(pk=request.POST.get("game_account") or None).first() \
            if _is_uuid(request.POST.get("game_account")) else None
        if game_account is None:
            return editor_response(request, base, notice="Escolha a conta.", notice_ok=False)
        own, _created = NotificationTemplate.objects.get_or_create(
            scope_key=NotificationTemplate.scope_for(base.event_key, game_account.pk),
            defaults={
                "event_key": base.event_key, "game_account": game_account, "icon": base.icon,
                "title_template": base.title_template, "body_template": base.body_template,
            },
        )
        return editor_response(request, base, notice=f"{game_account.name or game_account.server_id} agora tem um texto próprio; edite-o na lista abaixo.")


class AccountTemplateDeleteView(LoginRequiredMixin, View):
    """POST: drop the text an account has of its own; it goes back to the global one."""

    def post(self, request, pk):
        own = get_object_or_404(NotificationTemplate, pk=pk, game_account__isnull=False)
        base = NotificationTemplate.for_event(own.event_key)
        name = own.game_account.name or own.game_account.server_id
        own.delete()
        if base is None:
            return HttpResponse("")
        return editor_response(request, base, notice=f"{name} voltou a usar o texto global.")


def _is_uuid(value) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
    except (TypeError, ValueError):
        return False
    return True


# ── Audit ───────────────────────────────────────────────────────────

class IncomingCommandSaveView(LoginRequiredMixin, View):
    """POST: save one configurable Telegram inbound command."""

    def post(self, request, pk):
        command = get_object_or_404(TelegramIncomingCommand, pk=pk)
        form = TelegramIncomingCommandForm(request.POST, instance=command)
        if form.is_valid():
            form.save()

        TelegramIncomingCommand.seed_defaults()
        command_items = [
            {
                "command": item,
                "form": form if item.pk == command.pk else TelegramIncomingCommandForm(instance=item),
                "label": item.get_key_display(),
            }
            for item in TelegramIncomingCommand.objects.all()
        ]
        html = render_to_string(
            "telegram/partials/incoming_commands.html",
            {"command_items": command_items},
            request=request,
        )
        response = HttpResponse(html)
        if form.is_valid():
            response["HX-Trigger"] = "showToast"
        return response


class TelegramAuditView(FilterSortListView):
    # N-104: the history says which notice each message was, by name
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        labels = {event["key"]: event["label"] for event in EVENT_TYPES}
        for message in ctx.get("object_list") or []:
            message.channel_label = labels.get(message.channel, message.channel)
        return ctx

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


class TelegramAuditBulkDeleteView(LoginRequiredMixin, View):
    """POST: exclui mensagens selecionadas do historico Telegram."""

    def post(self, request):
        pks = request.POST.getlist("selected_ids")
        if not pks:
            resp = HttpResponse(status=200)
            resp["HX-Trigger"] = json.dumps({
                "toast": {"type": "error", "message": "Nenhuma mensagem selecionada."},
            })
            return resp

        deleted, _ = MessageAudit.objects.filter(pk__in=pks).delete()
        resp = HttpResponse(status=200)
        resp["HX-Trigger"] = json.dumps({
            "toast": {"type": "success", "message": f"{deleted} mensagem(ns) excluida(s)."},
            "telegramMessagesChanged": True,
        })
        return resp


class TelegramAuditRetryView(LoginRequiredMixin, View):
    """POST: reenviar notificação que falhou usando context_json salvo."""

    def post(self, request, pk):
        from apps.telegram.models import MessageAudit
        from apps.telegram.services.notifications import notify

        audit = get_object_or_404(MessageAudit, pk=pk)
        if audit.status not in ("failed",):
            resp = HttpResponse(status=400)
            resp["HX-Trigger"] = json.dumps({"toast": {"type": "error", "message": "Só notificações com falha podem ser reenviadas."}})
            return resp

        try:
            ctx = json.loads(audit.context_json or "{}")
        except Exception:
            ctx = {}

        if not ctx:
            resp = HttpResponse(status=400)
            resp["HX-Trigger"] = json.dumps({"toast": {"type": "error", "message": "Contexto não disponível para reenvio (notificação antiga)."}})
            return resp

        ok = notify(
            event_key=audit.channel,
            game_account=audit.game_account,
            account=audit.account,
            node=audit.node,
            job=audit.job,
            agent_name=audit.agent_name or "",
            **{k: v for k, v in ctx.items() if k not in ("ga_name", "server_id", "account_name", "node_name", "agent_name", "title", "body", "exit_code", "job_id", "status", "error", "action_name")},
        )

        if ok:
            audit.status = "retried"
            audit.save(update_fields=["status"])

        resp = HttpResponse(status=200)
        resp["HX-Trigger"] = json.dumps({
            "toast": {"type": "success" if ok else "error", "message": "Reenviado com sucesso." if ok else "Falha ao reenviar."},
            "telegramMessagesChanged": True,
        })
        return resp


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
