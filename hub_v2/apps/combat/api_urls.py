from django.urls import path
from .api.views import CombatRecommendView, CombatReportSaveView

app_name = "agent-combat"

urlpatterns = [
    path("combat/reports/", CombatReportSaveView.as_view(), name="combat-reports-save"),
    path("combat/recommend/", CombatRecommendView.as_view(), name="combat-recommend"),
]
