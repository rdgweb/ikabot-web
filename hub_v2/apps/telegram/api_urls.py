"""
API URL patterns for the Telegram app.

Mounted at /api/telegram/ in root urls.py.
"""

from django.urls import path

from .api.webhook import TelegramWebhookView

urlpatterns = [
    path(
        "webhook/<str:secret>/",
        TelegramWebhookView.as_view(),
        name="telegram-webhook",
    ),
]
