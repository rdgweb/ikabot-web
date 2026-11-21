from django.urls import path

from .api import NextView, RouteReportView

app_name = "agent-piracy"

urlpatterns = [
    path("piracy/next/", NextView.as_view(), name="next"),
    path("piracy/routes/<uuid:route_id>/", RouteReportView.as_view(), name="route-report"),
]
