from django.urls import path
from .api.views import CombatReportSaveView

app_name = "agent-combat"

urlpatterns = [
    path("combat/reports/", CombatReportSaveView.as_view(), name="combat-reports-save"),
]
