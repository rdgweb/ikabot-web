from django.urls import path

from .api.agent import AgentConfigView, AgentHeartbeatView, AgentRegisterView, AgentSessionView

app_name = "agent-accounts"

urlpatterns = [
    path("register/", AgentRegisterView.as_view(), name="register"),
    path("heartbeat/", AgentHeartbeatView.as_view(), name="heartbeat"),
    path("config/", AgentConfigView.as_view(), name="config"),
    path("sessions/", AgentSessionView.as_view(), name="sessions"),
]
