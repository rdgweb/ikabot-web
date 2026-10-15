from django.urls import path

from .views import (
    ActionCatalogView,
    CheckAllStatusView,
    ConstructionPanelView,
    DashboardHistoryView,
    DashboardView,
    RenameCityView,
    ReorderBuildingsView,
    RunActionView,
)

app_name = "game"

urlpatterns = [
    path("", DashboardView.as_view(), name="dashboard"),
    path("history/", DashboardHistoryView.as_view(), name="dashboard-history"),
    path("run-action/", RunActionView.as_view(), name="run-action"),
    path("check-status-all/", CheckAllStatusView.as_view(), name="check-status-all"),
    path("rename-city/", RenameCityView.as_view(), name="rename-city"),
    path("reorder-buildings/", ReorderBuildingsView.as_view(), name="reorder-buildings"),
    path("actions/", ActionCatalogView.as_view(), name="action-catalog"),
    path("construction/", ConstructionPanelView.as_view(), name="construction"),
]
