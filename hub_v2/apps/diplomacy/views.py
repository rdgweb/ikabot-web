"""
Diplomacy views — Central de Mensagens.
"""

from __future__ import annotations

import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
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
