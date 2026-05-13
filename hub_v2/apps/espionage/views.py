"""
Espionagem views — lista de relatórios de espionagem.
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views import View

from core.mixins.views import FilterSortListView

from .filters import SpyReportFilter
from .models import SpyReport


class SpyReportListView(FilterSortListView):
    """Lista de relatórios de espionagem com filtros."""

    model = SpyReport
    filterset_class = SpyReportFilter
    template_name = "espionage/list.html"
    partial_template_name = "espionage/partials/table.html"
    paginate_by = 25
    ordering_fields = [
        "target_owner", "target_city_name", "subject",
        "result_status", "agents_sent", "agents_lost", "date_str", "created_at",
    ]
    default_ordering = "-created_at"
    queryset = SpyReport.objects.select_related(
        "game_account", "game_account__account"
    )


class SpyReportDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        SpyReport.objects.filter(pk=pk).delete()
        response = HttpResponse(status=204)
        response["HX-Redirect"] = (
            request.META.get("HTTP_REFERER") or reverse("espionage:list")
        )
        return response


class SpyReportBulkDeleteView(LoginRequiredMixin, View):
    def post(self, request):
        ids = request.POST.getlist("ids")
        if ids:
            SpyReport.objects.filter(pk__in=ids).delete()
        return redirect("espionage:list")
