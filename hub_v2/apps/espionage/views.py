"""
Espionagem views — lista de relatórios de espionagem.
"""

from __future__ import annotations

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
