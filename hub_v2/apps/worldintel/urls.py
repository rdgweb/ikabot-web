from django.urls import path

from .views import (
    WorldCityDetailView, WorldDumpDeleteView, WorldDumpListView, WorldIslandListView, WorldMapDataView, WorldMapIslandView,
    WorldMapSearchView, WorldMapView, WorldPlayerDetailView, WorldPlayerListView, PlayerMarkToggleView, WorldTargetRaidView,
    WorldTargetsView,
)

app_name = "worldintel"

urlpatterns = [
    path("", WorldPlayerListView.as_view(), name="home"),
    path("dumps/", WorldDumpListView.as_view(), name="dumps"),
    path("dumps/<uuid:pk>/delete/", WorldDumpDeleteView.as_view(), name="dump-delete"),
    path("players/", WorldPlayerListView.as_view(), name="players"),
    path("players/detail/", WorldPlayerDetailView.as_view(), name="player-detail"),
    path("players/city/", WorldCityDetailView.as_view(), name="city-detail"),
    path("islands/", WorldIslandListView.as_view(), name="islands"),
    path("map/", WorldMapView.as_view(), name="map"),
    path("map/data/", WorldMapDataView.as_view(), name="map-data"),
    path("map/island/", WorldMapIslandView.as_view(), name="map-island"),
    path("map/search/", WorldMapSearchView.as_view(), name="map-search"),
    path("players/mark/", PlayerMarkToggleView.as_view(), name="player-mark"),
    path("targets/", WorldTargetsView.as_view(), name="targets"),
    path("targets/raid/", WorldTargetRaidView.as_view(), name="target-raid"),
]

