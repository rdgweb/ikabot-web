from django.urls import path

from .views import (
    ActionCatalogView,
    ConstructionPanelView,
    DashboardHistoryView,
    DashboardView,
    RenameCityView,
    RunActionView,
)

app_name = "game"

urlpatterns = [
    path("", DashboardView.as_view(), name="dashboard"),
    path("history/", DashboardHistoryView.as_view(), name="dashboard-history"),
    path("run-action/", RunActionView.as_view(), name="run-action"),
    path("rename-city/", RenameCityView.as_view(), name="rename-city"),
    path("actions/", ActionCatalogView.as_view(), name="action-catalog"),
    path("construction/", ConstructionPanelView.as_view(), name="construction"),
]
