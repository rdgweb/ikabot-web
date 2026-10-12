from django.urls import path

from .views import DashboardLiveView, DashboardView

app_name = "dashboard"

urlpatterns = [
    path("", DashboardView.as_view(), name="index"),
    path("dashboard/live/", DashboardLiveView.as_view(), name="live"),
]
