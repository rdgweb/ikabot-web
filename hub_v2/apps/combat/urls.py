from django.urls import path

from .views import CombatDetailView, CombatListView, CombatRoundImageView, CombatSendView, SimulatorActionView, SimulatorView

app_name = "combat"

urlpatterns = [
    path("", CombatListView.as_view(), name="list"),
    path("simulador/", SimulatorView.as_view(), name="simulator"),
    path("simulador/acao/", SimulatorActionView.as_view(), name="simulator-action"),
    path("<uuid:pk>/", CombatDetailView.as_view(), name="detail"),
    path("<uuid:pk>/round/<int:number>.jpg", CombatRoundImageView.as_view(), name="round-image"),
    path("<uuid:pk>/send/", CombatSendView.as_view(), name="send"),
]
