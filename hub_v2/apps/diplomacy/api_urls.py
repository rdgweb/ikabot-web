from django.urls import path

from .api import DiplomacyMessagesSaveView

app_name = "agent-diplomacy"

urlpatterns = [
    path("diplomacy/messages/", DiplomacyMessagesSaveView.as_view(), name="diplomacy-messages-save"),
]
