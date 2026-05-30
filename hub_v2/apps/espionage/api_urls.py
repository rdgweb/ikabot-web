from django.urls import path

from .api.views import MissionsCoveredView, ScanRaidAlertsView, SpyIntelView, SpyReportsSaveView

app_name = "agent-espionage"

urlpatterns = [
    path("espionage/reports/", SpyReportsSaveView.as_view(), name="espionage-reports-save"),
    path("espionage/intel/",   SpyIntelView.as_view(),       name="espionage-intel"),
    path("espionage/missions-covered/", MissionsCoveredView.as_view(), name="espionage-missions-covered"),
    path("espionage/scan-raid-alerts/", ScanRaidAlertsView.as_view(), name="espionage-scan-raid-alerts"),
]
