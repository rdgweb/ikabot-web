from django.urls import path

from .api.agent import BlackboxTokenView, CurrentSnapshotView, SolveCaptchaView, UpdateSnapshotView

app_name = "agent-game"

urlpatterns = [
    path("snapshots/", UpdateSnapshotView.as_view(), name="snapshots"),
    path("snapshots/current/", CurrentSnapshotView.as_view(), name="snapshots-current"),
    path("blackbox/token/", BlackboxTokenView.as_view(), name="blackbox-token"),
    path("captcha/solve/", SolveCaptchaView.as_view(), name="captcha-solve"),
]
