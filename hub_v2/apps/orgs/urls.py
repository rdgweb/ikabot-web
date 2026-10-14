from django.urls import path

from .views import OrganizationTelegramView

app_name = "orgs"

urlpatterns = [
    path("telegram/", OrganizationTelegramView.as_view(), name="telegram"),
]
