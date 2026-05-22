from django.urls import path

from .views import (
    SettingsPageView,
    SettingEditView,
    WebshareSaveView,
    WebshareTestView,
    IkabotApiSaveView,
    IkabotApiTestView,
    AgentSecuritySaveView,
    SnapshotPolicySaveView,
    AutoArchiveWorkflowsView,
    SaveMaxRunsView,
)

app_name = "settings_app"

urlpatterns = [
    path("", SettingsPageView.as_view(), name="list"),
    path("<str:key>/edit/", SettingEditView.as_view(), name="edit"),
    path("webshare/save/", WebshareSaveView.as_view(), name="webshare-save"),
    path("webshare/test/", WebshareTestView.as_view(), name="webshare-test"),
    path("ikabotapi/save/", IkabotApiSaveView.as_view(), name="ikabotapi-save"),
    path("ikabotapi/test/", IkabotApiTestView.as_view(), name="ikabotapi-test"),
    path("agent/save/", AgentSecuritySaveView.as_view(), name="agent-save"),
    path("snapshot-policy/save/", SnapshotPolicySaveView.as_view(), name="snapshot-policy-save"),
    path("workflows/auto-archive/", AutoArchiveWorkflowsView.as_view(), name="auto-archive-workflows"),
    path("workflows/max-runs/save/", SaveMaxRunsView.as_view(), name="save-max-runs"),
]
