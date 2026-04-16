from django.urls import path

from .views import (
    NodeListView, NodeDetailView, NodeCreateView, NodeEditView, NodeDeleteView, NodeDeployView,
    NodeToggleView, NodeProxyTestView,
    AccountListView, AccountDetailView, AccountCreateView, AccountEditView, AccountDeleteView,
    AccountToggleView,
    GameAccountToggleView,
    GameAccountBuildTimeView,
    GameAccountGovernmentTimeView,
    GameAccountMarketToggleView,
    GameAccountMarketStockView,
    GameAccountMarketGoldView,
)

app_name = "accounts"

urlpatterns = [
    # Nodes
    path("nodes/", NodeListView.as_view(), name="node-list"),
    path("nodes/create/", NodeCreateView.as_view(), name="node-create"),
    path("nodes/<uuid:pk>/", NodeDetailView.as_view(), name="node-detail"),
    path("nodes/<uuid:pk>/edit/", NodeEditView.as_view(), name="node-edit"),
    path("nodes/<uuid:pk>/delete/", NodeDeleteView.as_view(), name="node-delete"),
    path("nodes/<uuid:pk>/toggle/", NodeToggleView.as_view(), name="node-toggle"),
    path("nodes/<uuid:pk>/deploy/", NodeDeployView.as_view(), name="node-deploy"),
    path("nodes/<uuid:pk>/proxy-test/", NodeProxyTestView.as_view(), name="node-proxy-test"),
    # Accounts
    path("", AccountListView.as_view(), name="account-list"),
    path("create/", AccountCreateView.as_view(), name="account-create"),
    path("<uuid:pk>/", AccountDetailView.as_view(), name="account-detail"),
    path("<uuid:pk>/edit/", AccountEditView.as_view(), name="account-edit"),
    path("<uuid:pk>/delete/", AccountDeleteView.as_view(), name="account-delete"),
    path("<uuid:pk>/toggle/", AccountToggleView.as_view(), name="account-toggle"),
    # Game Accounts
    path("game-account/<uuid:pk>/toggle/", GameAccountToggleView.as_view(), name="game-account-toggle"),
    path("game-account/<uuid:pk>/build-time/", GameAccountBuildTimeView.as_view(), name="game-account-build-time"),
    path("game-account/<uuid:pk>/government-time/", GameAccountGovernmentTimeView.as_view(), name="game-account-government-time"),
    path("game-account/<uuid:pk>/market-toggle/", GameAccountMarketToggleView.as_view(), name="game-account-market-toggle"),
    path("game-account/<uuid:pk>/market-stock-limit/", GameAccountMarketStockView.as_view(), name="game-account-market-stock"),
    path("game-account/<uuid:pk>/market-gold-limit/", GameAccountMarketGoldView.as_view(), name="game-account-market-gold"),
]
