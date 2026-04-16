from django.urls import path

from .api import MarketOrderCompleteView, MarketOrderCreateView, MarketSellCompleteView

app_name = "agent-market"

urlpatterns = [
    path("market/orders/create/", MarketOrderCreateView.as_view(), name="order-create"),
    path("market/orders/<uuid:order_id>/sell-complete/", MarketSellCompleteView.as_view(), name="sell-complete"),
    path("market/orders/<uuid:order_id>/complete/", MarketOrderCompleteView.as_view(), name="order-complete"),
]
