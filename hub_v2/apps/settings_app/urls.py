from django.urls import path
from django.views.generic import RedirectView

from .views import (
    SettingsPageView,
    SystemHealthView,
    TourDoneView,
    TourStateView,
    TourToggleView,
    SettingEditView,
    WebshareSaveView,
    WebshareTestView,
    IkabotApiSaveView,
    IkabotApiTestView,
    AgentSecuritySaveView,
    AgentDeploySettingsSaveView,
    SnapshotPolicySaveView,
    AutoArchiveWorkflowsView,
    SaveMaxRunsView,
    SaveSpyIntelSettingsView,
)

app_name = "settings_app"

urlpatterns = [
    path("", SettingsPageView.as_view(), name="list"),
    path("saude/", SystemHealthView.as_view(), name="health"),
    path("primeiros-passos/", RedirectView.as_view(url="/settings/#saude"), name="first-steps"),
    path("tour/done/", TourDoneView.as_view(), name="tour-done"),
    path("tour/state/", TourStateView.as_view(), name="tour-state"),
    path("tour/toggle/", TourToggleView.as_view(), name="tour-toggle"),
    path("<str:key>/edit/", SettingEditView.as_view(), name="edit"),
    path("webshare/save/", WebshareSaveView.as_view(), name="webshare-save"),
    path("webshare/test/", WebshareTestView.as_view(), name="webshare-test"),
    path("ikabotapi/save/", IkabotApiSaveView.as_view(), name="ikabotapi-save"),
    path("ikabotapi/test/", IkabotApiTestView.as_view(), name="ikabotapi-test"),
    path("agent/save/", AgentSecuritySaveView.as_view(), name="agent-save"),
    path("agent/deploy/save/", AgentDeploySettingsSaveView.as_view(), name="agent-deploy-save"),
    path("snapshot-policy/save/", SnapshotPolicySaveView.as_view(), name="snapshot-policy-save"),
    path("workflows/auto-archive/", AutoArchiveWorkflowsView.as_view(), name="auto-archive-workflows"),
    path("workflows/max-runs/save/", SaveMaxRunsView.as_view(), name="save-max-runs"),
    path("spy-intel/save/", SaveSpyIntelSettingsView.as_view(), name="spy-intel-save"),
]
