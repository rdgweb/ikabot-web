from django.urls import path

from .api.agent import (
    BlackboxTokenView,
    CurrentSnapshotView,
    PatchSnapshotBuildingView,
    PatchSnapshotResourcesView,
    SolveCaptchaView,
    UpdateSnapshotView,
)

app_name = "agent-game"

urlpatterns = [
    path("snapshots/", UpdateSnapshotView.as_view(), name="snapshots"),
    path("snapshots/current/", CurrentSnapshotView.as_view(), name="snapshots-current"),
    path("snapshots/patch-building/", PatchSnapshotBuildingView.as_view(), name="snapshots-patch-building"),
    path("snapshots/patch-resources/", PatchSnapshotResourcesView.as_view(), name="snapshots-patch-resources"),
    path("blackbox/token/", BlackboxTokenView.as_view(), name="blackbox-token"),
    path("captcha/solve/", SolveCaptchaView.as_view(), name="captcha-solve"),
]
