from django.urls import path

from .views import (
    MarketDashboardView,
    MarketOrdersPartialView,
    MarketParticipantsPartialView,
    MarketOrderDetailView,
    MarketOrderCreateView,
    MarketOrderCancelView,
)

app_name = "market"

urlpatterns = [
    path("", MarketDashboardView.as_view(), name="dashboard"),
    path("orders/", MarketOrdersPartialView.as_view(), name="orders-partial"),
    path("orders/create/", MarketOrderCreateView.as_view(), name="order-create"),
    path("orders/<uuid:pk>/cancel/", MarketOrderCancelView.as_view(), name="order-cancel"),
    path("participants/", MarketParticipantsPartialView.as_view(), name="participants-partial"),
    path("<uuid:pk>/", MarketOrderDetailView.as_view(), name="order-detail"),
]
