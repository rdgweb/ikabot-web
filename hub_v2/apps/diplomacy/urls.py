from django.urls import path

from .views import DiplomacyBulkDeleteView, DiplomacyInboxView

app_name = "diplomacy"

urlpatterns = [
    path("", DiplomacyInboxView.as_view(), name="inbox"),
    path("messages/", DiplomacyInboxView.as_view(), name="messages-partial"),
    path("messages/delete/", DiplomacyBulkDeleteView.as_view(), name="bulk-delete"),
]
