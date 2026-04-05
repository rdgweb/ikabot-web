"""
Market views — order list and detail.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import DetailView

from core.mixins.views import FilterSortListView
from .models import InternalMarketOrder
from .filters import MarketOrderFilter


class MarketOrderListView(FilterSortListView):
    model = InternalMarketOrder
    filterset_class = MarketOrderFilter
    template_name = "market/order_list.html"
    partial_template_name = "market/partials/order_table.html"
    paginate_by = 25
    ordering_fields = ["status", "resource_idx", "amount", "created_at"]
    default_ordering = "-created_at"
    queryset = InternalMarketOrder.objects.select_related(
        "buyer_account", "seller_account", "buyer_node"
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
                "seller_account",
                "buyer_node",
                "seller_node",
                "buy_job",
                "sell_job",
                "redistribution_job",
            )
        )
