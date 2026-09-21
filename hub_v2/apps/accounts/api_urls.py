from django.urls import path

from .api.agent import (
    AccountLobbyProxiesView,
    AgentConfigView,
    AgentHeartbeatView,
    AgentRegisterView,
    AgentSessionView,
    AgentUpdateNextView,
    AgentUpdateStatusView,
)

app_name = "agent-accounts"

urlpatterns = [
    path("register/", AgentRegisterView.as_view(), name="register"),
    path("heartbeat/", AgentHeartbeatView.as_view(), name="heartbeat"),
    path("config/", AgentConfigView.as_view(), name="config"),
    path("updates/next/", AgentUpdateNextView.as_view(), name="update-next"),
    path("updates/<uuid:update_id>/status/", AgentUpdateStatusView.as_view(), name="update-status"),
    path("accounts/<uuid:account_id>/lobby-proxies/", AccountLobbyProxiesView.as_view(), name="lobby-proxies"),
    path("sessions/", AgentSessionView.as_view(), name="sessions"),
]
