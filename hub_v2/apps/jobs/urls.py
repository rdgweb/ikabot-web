from django.urls import path

from .views import (
    JobListView,
    JobDetailView,
    WorkflowDetailView,
    WorkflowRunsPartialView,
    WorkflowRunJobsPartialView,
    WorkflowLogsPartialView,
    WorkflowActionView,
    WorkflowArchiveView,
    WorkflowAutoArchiveView,
    WorkflowBulkArchiveView,
    WorkflowBulkDeleteView,
    JobLogsPartialView,
    JobChainHistoryPartialView,
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
    path("workflows/<uuid:pk>/", WorkflowDetailView.as_view(), name="workflow-detail"),
    path("workflows/<uuid:pk>/runs/", WorkflowRunsPartialView.as_view(), name="workflow-runs"),
    path("workflows/<uuid:pk>/runs/<uuid:run_pk>/jobs/", WorkflowRunJobsPartialView.as_view(), name="workflow-run-jobs"),
    path("workflows/<uuid:pk>/logs/", WorkflowLogsPartialView.as_view(), name="workflow-logs"),
    path("workflows/<uuid:pk>/action/", WorkflowActionView.as_view(), name="workflow-action"),
    path("workflows/<uuid:pk>/archive/", WorkflowArchiveView.as_view(), name="workflow-archive"),
    path("workflows/auto-archive/", WorkflowAutoArchiveView.as_view(), name="workflow-auto-archive"),
    path("bulk-delete/", JobBulkDeleteView.as_view(), name="job-bulk-delete"),
    path("workflows/bulk-delete/", WorkflowBulkDeleteView.as_view(), name="workflow-bulk-delete"),
    path("workflows/bulk-archive/", WorkflowBulkArchiveView.as_view(), name="workflow-bulk-archive"),
    # Job creation modal flow
    path("new/", JobCreateModalView.as_view(), name="job-create"),
    path("new/actions/", JobActionPickerView.as_view(), name="job-actions"),
    path("new/form/", JobFormView.as_view(), name="job-form"),
    path("new/construction-preview/", ConstructionPlanPreviewView.as_view(), name="job-construction-preview"),
    path("new/submit/", JobSubmitView.as_view(), name="job-submit"),
    # Job detail
    path("<uuid:pk>/", JobDetailView.as_view(), name="job-detail"),
    path("<uuid:pk>/chain-history/", JobChainHistoryPartialView.as_view(), name="job-chain-history"),
    path("<uuid:pk>/logs/", JobLogsPartialView.as_view(), name="job-logs"),
    path("<uuid:pk>/cancel/", JobCancelView.as_view(), name="job-cancel"),
    path("<uuid:pk>/run-now/", JobRunNowView.as_view(), name="job-run-now"),
    path("<uuid:pk>/retry/", JobRetryView.as_view(), name="job-retry"),
]
