from django.urls import path

from .views import WorldDumpDeleteView, WorldDumpListView, WorldIslandListView, WorldPlayerDetailView, WorldPlayerListView

app_name = "worldintel"

urlpatterns = [
    path("", WorldPlayerListView.as_view(), name="home"),
    path("dumps/", WorldDumpListView.as_view(), name="dumps"),
    path("dumps/<uuid:pk>/delete/", WorldDumpDeleteView.as_view(), name="dump-delete"),
    path("players/", WorldPlayerListView.as_view(), name="players"),
    path("players/detail/", WorldPlayerDetailView.as_view(), name="player-detail"),
    path("islands/", WorldIslandListView.as_view(), name="islands"),
]

