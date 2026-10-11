from django.urls import path

from .views import ControlChipView, EndControlView, SessionPageView, SessionStatusView, StartControlView

app_name = "manual_control"

urlpatterns = [
    path("start/<uuid:game_account_id>/", StartControlView.as_view(), name="start"),
    path("chip/<uuid:game_account_id>/", ControlChipView.as_view(), name="chip"),
    path("<uuid:session_id>/", SessionPageView.as_view(), name="session"),
    path("<uuid:session_id>/status/", SessionStatusView.as_view(), name="status"),
    path("<uuid:session_id>/end/", EndControlView.as_view(), name="end"),
]
