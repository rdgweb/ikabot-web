from django.urls import path

from .views import MarketOrderListView, MarketOrderDetailView

app_name = "market"

urlpatterns = [
    path("", MarketOrderListView.as_view(), name="order-list"),
    path("<uuid:pk>/", MarketOrderDetailView.as_view(), name="order-detail"),
]
