from django.urls import path

from .api.views import SpyReportsSaveView

app_name = "agent-espionage"

urlpatterns = [
    path("espionage/reports/", SpyReportsSaveView.as_view(), name="espionage-reports-save"),
]
