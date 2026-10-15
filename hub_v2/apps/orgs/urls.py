from django.urls import path

from .owner_views import MembersView, OverviewView, SubaccountsView
from .views import OrganizationTelegramView

app_name = "orgs"

urlpatterns = [
    path("", OverviewView.as_view(), name="overview"),
    path("subcontas/", SubaccountsView.as_view(), name="subaccounts"),
    path("membros/", MembersView.as_view(), name="members"),
    path("telegram/", OrganizationTelegramView.as_view(), name="telegram"),
]
