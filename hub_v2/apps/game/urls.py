from django.urls import path
from django.views.generic import TemplateView

from .views import ActionCatalogView, DashboardHistoryView, DashboardView, RunActionView

app_name = "game"

urlpatterns = [
    path("", DashboardView.as_view(), name="dashboard"),
    path("history/", DashboardHistoryView.as_view(), name="dashboard-history"),
    path("run-action/", RunActionView.as_view(), name="run-action"),
    path("actions/", ActionCatalogView.as_view(), name="action-catalog"),
    path(
        "construction/",
        TemplateView.as_view(template_name="game/construction.html"),
        name="construction",
    ),
]
