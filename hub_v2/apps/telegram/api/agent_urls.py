"""
Agent-facing API URLs for Telegram notifications.

Mounted at /api/agent/ in root urls.py.
"""

from django.urls import path

from .notify import AgentNotifyView

app_name = "agent-telegram"

urlpatterns = [
    path("notify/", AgentNotifyView.as_view(), name="notify"),
]
