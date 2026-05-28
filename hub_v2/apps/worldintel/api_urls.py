from django.urls import path

from .api.agent import WorldDumpAppendView, WorldDumpCreateView, WorldDumpReplaceIslandsView, WorldSpyTargetsView

app_name = "agent-worldintel"

urlpatterns = [
    path("world-dumps/", WorldDumpCreateView.as_view(), name="world-dumps"),
    path("world-dumps/<uuid:dump_id>/append/", WorldDumpAppendView.as_view(), name="world-dump-append"),
    path("world-dumps/<uuid:dump_id>/replace-islands/", WorldDumpReplaceIslandsView.as_view(), name="world-dump-replace-islands"),
    path("worldintel/spy-targets/", WorldSpyTargetsView.as_view(), name="spy-targets"),
]
