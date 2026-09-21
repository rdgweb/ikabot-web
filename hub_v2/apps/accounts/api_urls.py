from django.urls import path

from .api.agent import (
    AccountLobbyProxiesView,
    AgentConfigView,
    AgentHeartbeatView,
    AgentRegisterView,
    AgentSessionView,
)
from .api.supervisor import SupervisorHeartbeatView, SupervisorUpdateNextView, SupervisorUpdateStatusView

app_name = "agent-accounts"

urlpatterns = [
    path("supervisor/heartbeat/", SupervisorHeartbeatView.as_view(), name="supervisor-heartbeat"),
    path("supervisor/updates/next/", SupervisorUpdateNextView.as_view(), name="supervisor-update-next"),
    path("supervisor/updates/<uuid:update_id>/status/", SupervisorUpdateStatusView.as_view(), name="supervisor-update-status"),
    path("register/", AgentRegisterView.as_view(), name="register"),
    path("heartbeat/", AgentHeartbeatView.as_view(), name="heartbeat"),
    path("config/", AgentConfigView.as_view(), name="config"),
    path("accounts/<uuid:account_id>/lobby-proxies/", AccountLobbyProxiesView.as_view(), name="lobby-proxies"),
    path("sessions/", AgentSessionView.as_view(), name="sessions"),
]
