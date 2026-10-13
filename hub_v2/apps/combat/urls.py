from django.urls import path

from .views import CombatDetailView, CombatListView, CombatRoundImageView

app_name = "combat"

urlpatterns = [
    path("", CombatListView.as_view(), name="list"),
    path("<uuid:pk>/", CombatDetailView.as_view(), name="detail"),
    path("<uuid:pk>/round/<int:number>.jpg", CombatRoundImageView.as_view(), name="round-image"),
]
