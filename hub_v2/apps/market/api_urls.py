from django.urls import path

from .api import (
    BlackMarketOfferPriceView,
    BlackMarketOfferSaveView,
    BlackMarketOfferSyncView,
    BlackMarketQuoteSaveView,
    ConstructionMarketInterventionRequestView,
    MarketOrderCompleteView,
    MarketOrderCreateView,
    MarketSellCompleteView,
)

app_name = "agent-market"

urlpatterns = [
    path("market/orders/create/", MarketOrderCreateView.as_view(), name="order-create"),
    path("market/orders/<uuid:order_id>/sell-complete/", MarketSellCompleteView.as_view(), name="sell-complete"),
    path("market/orders/<uuid:order_id>/complete/", MarketOrderCompleteView.as_view(), name="order-complete"),
    path("market/interventions/request/", ConstructionMarketInterventionRequestView.as_view(), name="intervention-request"),
    path("market/bm-offers/", BlackMarketOfferSaveView.as_view(), name="bm-offer-save"),
    path("market/bm-offers/sync/", BlackMarketOfferSyncView.as_view(), name="bm-offer-sync"),
    path("market/bm-offers/prices/", BlackMarketOfferPriceView.as_view(), name="bm-offer-prices"),
    path("market/bm-quotes/", BlackMarketQuoteSaveView.as_view(), name="bm-quote-save"),
]
