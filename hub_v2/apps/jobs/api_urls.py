from django.urls import path

from .api.agent import (
    ConstructionSupportView,
    JobLogView,
    JobStatusReadView,
    RetimeRootFollowupJobView,
    JobStatusView,
    RescheduleJobView,
    SpawnJobView,
)

app_name = "agent-jobs"

urlpatterns = [
    path("jobs/<uuid:job_id>/status/", JobStatusView.as_view(), name="status"),
    path("jobs/<uuid:job_id>/logs/", JobLogView.as_view(), name="logs"),
    path("jobs/<uuid:job_id>/reschedule/", RescheduleJobView.as_view(), name="reschedule"),
    path("jobs/retime-followup/", RetimeRootFollowupJobView.as_view(), name="retime-followup"),
    path("jobs/<uuid:job_id>/spawn/", SpawnJobView.as_view(), name="spawn"),
    path("jobs/<uuid:job_id>/info/", JobStatusReadView.as_view(), name="info"),
    path("jobs/<uuid:job_id>/construction-support/", ConstructionSupportView.as_view(), name="construction-support"),
]
