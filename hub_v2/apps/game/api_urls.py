from django.urls import path

from .api.agent import (
    BlackboxTokenView,
    CaptchaChallengeCreateView,
    CaptchaChallengePollView,
    CurrentSnapshotView,
    MilitaryMovementsUpdateView,
    PatchSnapshotBaseView,
    PatchSnapshotBuildingView,
    PatchSnapshotResourcesView,
    PatchSnapshotGoldView,
    PatchSnapshotShipsView,
    SolveCaptchaView,
    UpdateSnapshotView,
)

app_name = "agent-game"

urlpatterns = [
    path("snapshots/", UpdateSnapshotView.as_view(), name="snapshots"),
    path("snapshots/current/", CurrentSnapshotView.as_view(), name="snapshots-current"),
    path("snapshots/patch-base/", PatchSnapshotBaseView.as_view(), name="snapshots-patch-base"),
    path("snapshots/patch-building/", PatchSnapshotBuildingView.as_view(), name="snapshots-patch-building"),
    path("snapshots/patch-resources/", PatchSnapshotResourcesView.as_view(), name="snapshots-patch-resources"),
    path("snapshots/patch-ships/", PatchSnapshotShipsView.as_view(), name="snapshots-patch-ships"),
    path("snapshots/patch-gold/", PatchSnapshotGoldView.as_view(), name="snapshots-patch-gold"),
    path("blackbox/token/", BlackboxTokenView.as_view(), name="blackbox-token"),
    path("captcha/solve/", SolveCaptchaView.as_view(), name="captcha-solve"),
    path("captcha/challenge/", CaptchaChallengeCreateView.as_view(), name="captcha-challenge-create"),
    path("captcha/challenge/<int:pk>/", CaptchaChallengePollView.as_view(), name="captcha-challenge-poll"),
    path("military-movements/", MilitaryMovementsUpdateView.as_view(), name="military-movements"),
]
