from django.urls import path

from .views import (
    BlackMarketDashboardView,
    BlackMarketOfferCloseView,
    MarketDashboardView,
    MarketOrderBulkCancelView,
    MarketOrderBulkDeleteView,
    MarketOrderCancelView,
    MarketOrderCreateView,
    MarketOrderDeleteView,
    MarketOrderDetailView,
    MarketOrdersPartialView,
    MarketParticipantsPartialView,
)

app_name = "market"

urlpatterns = [
    path("", MarketDashboardView.as_view(), name="dashboard"),
    path("black-market/", BlackMarketDashboardView.as_view(), name="black-market"),
    path("black-market/offers/<uuid:pk>/close/", BlackMarketOfferCloseView.as_view(), name="bm-offer-close"),
    path("orders/", MarketOrdersPartialView.as_view(), name="orders-partial"),
    path("orders/create/", MarketOrderCreateView.as_view(), name="order-create"),
    path("orders/<uuid:pk>/cancel/", MarketOrderCancelView.as_view(), name="order-cancel"),
    path("orders/cancel/", MarketOrderBulkCancelView.as_view(), name="order-bulk-cancel"),
    path("orders/delete/", MarketOrderBulkDeleteView.as_view(), name="order-bulk-delete"),
    path("orders/<uuid:pk>/delete/", MarketOrderDeleteView.as_view(), name="order-delete"),
    path("participants/", MarketParticipantsPartialView.as_view(), name="participants-partial"),
    path("<uuid:pk>/", MarketOrderDetailView.as_view(), name="order-detail"),
]
