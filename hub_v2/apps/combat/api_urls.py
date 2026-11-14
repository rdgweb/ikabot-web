from django.urls import path
from .api.views import BarbarianPlanView, BarbarianReinforceView, CombatRecommendView, CombatReportSaveView

app_name = "agent-combat"

urlpatterns = [
    path("combat/reports/", CombatReportSaveView.as_view(), name="combat-reports-save"),
    path("combat/recommend/", CombatRecommendView.as_view(), name="combat-recommend"),
    path("combat/barbarians/plan/", BarbarianPlanView.as_view(), name="combat-barbarians-plan"),
    path("combat/barbarians/reinforce/", BarbarianReinforceView.as_view(), name="combat-barbarians-reinforce"),
]
