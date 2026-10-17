"""
Espionagem views — lista de relatórios de espionagem.
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import redirect
from django.db.models import Q
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

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        attach_target_players(ctx.get("object_list") or [])
        return ctx


def attach_target_players(reports) -> None:
    """The target player's alliance and total score from the latest world dump (by id, or by name when the report
    has no id), and that dump for the links to Inteligência."""
    from apps.worldintel.models import WorldDump, WorldDumpPlayer

    reports = list(reports)
    dump = WorldDump.objects.order_by("-captured_at").first()
    by_id: dict[str, WorldDumpPlayer] = {}
    by_name: dict[str, WorldDumpPlayer] = {}
    if dump is not None:
        ids = {r.target_owner_id for r in reports if r.target_owner_id}
        names = {r.target_owner for r in reports if r.target_owner and not r.target_owner_id}
        rows = WorldDumpPlayer.objects.filter(dump=dump).filter(Q(owner_id__in=ids) | Q(owner_name__in=names))
        for row in rows:
            by_id[row.owner_id] = row
            by_name[row.owner_name.lower()] = row
    for report in reports:
        player = by_id.get(report.target_owner_id or "") or by_name.get((report.target_owner or "").lower())
        report.target_ally_tag = player.ally_tag if player else ""
        report.target_score = player.total_score if player else None
        report.target_owner_key = player.owner_id if player else (report.target_owner_id or "")
        report.intel_dump = str(dump.pk) if dump is not None else ""
        report.target_in_dump = player is not None


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
