from django.urls import path

from .views import (
    JobListView,
    JobDetailView,
    JobLogsPartialView,
    JobCancelView,
    JobRunNowView,
    JobRetryView,
    JobBulkDeleteView,
    JobCreateModalView,
    JobActionPickerView,
    JobFormView,
    JobSubmitView,
    ConstructionPlanPreviewView,
)

app_name = "jobs"

urlpatterns = [
    path("", JobListView.as_view(), name="job-list"),
    path("bulk-delete/", JobBulkDeleteView.as_view(), name="job-bulk-delete"),
    # Job creation modal flow
    path("new/", JobCreateModalView.as_view(), name="job-create"),
    path("new/actions/", JobActionPickerView.as_view(), name="job-actions"),
    path("new/form/", JobFormView.as_view(), name="job-form"),
    path("new/construction-preview/", ConstructionPlanPreviewView.as_view(), name="job-construction-preview"),
    path("new/submit/", JobSubmitView.as_view(), name="job-submit"),
    # Job detail
    path("<uuid:pk>/", JobDetailView.as_view(), name="job-detail"),
    path("<uuid:pk>/logs/", JobLogsPartialView.as_view(), name="job-logs"),
    path("<uuid:pk>/cancel/", JobCancelView.as_view(), name="job-cancel"),
    path("<uuid:pk>/run-now/", JobRunNowView.as_view(), name="job-run-now"),
    path("<uuid:pk>/retry/", JobRetryView.as_view(), name="job-retry"),
]
