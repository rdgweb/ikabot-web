from django.urls import path

from .views import SpyReportBulkDeleteView, SpyReportDeleteView, SpyReportListView

app_name = "espionage"

urlpatterns = [
    path("", SpyReportListView.as_view(), name="list"),
    path("table/", SpyReportListView.as_view(), name="table-partial"),
    path("<uuid:pk>/delete/", SpyReportDeleteView.as_view(), name="delete"),
    path("bulk-delete/", SpyReportBulkDeleteView.as_view(), name="bulk-delete"),
]
