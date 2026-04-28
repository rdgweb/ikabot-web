"""
Diplomacy views — Central de Mensagens.
"""

from __future__ import annotations

import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views import View

from core.mixins.views import FilterSortListView

from .filters import DiplomacyMessageFilter
from .models import DiplomacyMessage

logger = logging.getLogger(__name__)


class DiplomacyInboxView(FilterSortListView):
    """Central de Mensagens — lista paginada com filtros."""

    model = DiplomacyMessage
    filterset_class = DiplomacyMessageFilter
    template_name = "diplomacy/inbox.html"
    partial_template_name = "diplomacy/partials/message_table.html"
    paginate_by = 25
    ordering_fields = ["sender", "subject", "status", "created_at", "game_date"]
    default_ordering = "-created_at"
    queryset = DiplomacyMessage.objects.select_related(
        "game_account", "game_account__account"
    )


class DiplomacyActionView(LoginRequiredMixin, View):
    """POST: aceitar, recusar ou responder uma mensagem de diplomacia."""

    def post(self, request, pk):
        from apps.telegram.api.webhook import _create_diplomacy_send_job_from_uuid
        msg = get_object_or_404(DiplomacyMessage, pk=pk)
        action = request.POST.get("action", "").strip()
        text = request.POST.get("text", "").strip()

        if msg.status in ("action_taken", "replied"):
            resp = HttpResponse(status=400)
            resp["HX-Trigger"] = json.dumps({"toast": {"type": "warning", "message": "Esta mensagem já foi respondida anteriormente."}})
            return resp

        if action == "accept":
            yes_no, reply_text = "yes", ""
        elif action == "decline":
            yes_no, reply_text = "no", ""
        elif action == "reply":
            yes_no, reply_text = "", text
        else:
            resp = HttpResponse(status=400)
            resp["HX-Trigger"] = json.dumps({"toast": {"type": "error", "message": "Ação inválida."}})
            return resp

        ok, err = _create_diplomacy_send_job_from_uuid(str(msg.pk), yes_no, reply_text)
        if ok:
            new_status = "replied" if action == "reply" else "action_taken"
            msg.status = new_status
            msg.save(update_fields=["status", "updated_at"])
            resp = HttpResponse(status=200)
            resp["HX-Trigger"] = json.dumps({
                "toast": {"type": "success", "message": "Job criado na fila."},
                "diplomacyMessagesChanged": True,
            })
        else:
            resp = HttpResponse(status=400)
            resp["HX-Trigger"] = json.dumps({"toast": {"type": "error", "message": err}})
        return resp


class DiplomacyBulkDeleteView(LoginRequiredMixin, View):
    """POST: exclui as mensagens selecionadas da Central."""

    def post(self, request):
        pks = request.POST.getlist("selected_ids")
        if not pks:
            resp = HttpResponse(status=200)
            resp["HX-Trigger"] = json.dumps({
                "toast": {"type": "error", "message": "Nenhuma mensagem selecionada."},
            })
            return resp

        deleted, _ = DiplomacyMessage.objects.filter(pk__in=pks).delete()
        resp = HttpResponse(status=200)
        resp["HX-Trigger"] = json.dumps({
            "toast": {"type": "success", "message": f"{deleted} mensagem(ns) excluída(s)."},
            "diplomacyMessagesChanged": True,
        })
        return resp
