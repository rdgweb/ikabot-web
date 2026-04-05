from django.urls import path

from .api.agent import (
    ConstructionSupportView,
    JobLogView,
    JobStatusView,
    RescheduleJobView,
    SpawnJobView,
)

app_name = "agent-jobs"

urlpatterns = [
    path("jobs/<uuid:job_id>/status/", JobStatusView.as_view(), name="status"),
    path("jobs/<uuid:job_id>/logs/", JobLogView.as_view(), name="logs"),
    path("jobs/<uuid:job_id>/reschedule/", RescheduleJobView.as_view(), name="reschedule"),
    path("jobs/<uuid:job_id>/spawn/", SpawnJobView.as_view(), name="spawn"),
    path("jobs/<uuid:job_id>/construction-support/", ConstructionSupportView.as_view(), name="construction-support"),
]
