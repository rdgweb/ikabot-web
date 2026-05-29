from django.urls import path

from .api.views import SpyIntelView, SpyReportsSaveView

app_name = "agent-espionage"

urlpatterns = [
    path("espionage/reports/", SpyReportsSaveView.as_view(), name="espionage-reports-save"),
    path("espionage/intel/",   SpyIntelView.as_view(),       name="espionage-intel"),
]
