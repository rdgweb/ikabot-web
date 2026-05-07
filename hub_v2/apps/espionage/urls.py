from django.urls import path

from .views import SpyReportListView

app_name = "espionage"

urlpatterns = [
    path("", SpyReportListView.as_view(), name="list"),
    path("table/", SpyReportListView.as_view(), name="table-partial"),
]
