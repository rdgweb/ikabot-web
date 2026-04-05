"""
Serializers for the Agent jobs API.
"""

from rest_framework import serializers

from apps.jobs.models import Job


class JobStatusUpdateSerializer(serializers.Serializer):
    """Validates a job status update from the agent."""

    status = serializers.ChoiceField(choices=Job.STATUS_CHOICES)
    exit_code = serializers.IntegerField(required=False, allow_null=True)
    agent = serializers.CharField(required=False, allow_blank=True, default="")
    progress = serializers.JSONField(required=False)


class JobStatusResponseSerializer(serializers.Serializer):
    """Response shape for job status update."""

    ok = serializers.BooleanField()
    job_id = serializers.UUIDField()
    status = serializers.CharField()


class JobLogEntrySerializer(serializers.Serializer):
    """Validates a single log entry."""

    level = serializers.ChoiceField(
        choices=["info", "debug", "warn", "error"],
        default="info",
    )
    message = serializers.CharField(allow_blank=True)


class JobLogBulkSerializer(serializers.Serializer):
    """
    Accepts either a single log entry or a list of entries.
    The view handles the single-vs-list logic before passing to this.
    """

    logs = JobLogEntrySerializer(many=True)


class JobRescheduleSerializer(serializers.Serializer):
    """Validates a job reschedule request."""

    delay_seconds = serializers.IntegerField(min_value=1)
    inputs = serializers.JSONField(required=False)


class JobRescheduleResponseSerializer(serializers.Serializer):
    """Response shape for job reschedule."""

    ok = serializers.BooleanField()
    new_job_id = serializers.UUIDField()
    scheduled_for = serializers.DateTimeField()


class JobSpawnSerializer(serializers.Serializer):
    """Create a child job inheriting account/game-account/node from a parent job."""

    action_code = serializers.IntegerField(min_value=1)
    inputs = serializers.JSONField(default=dict)
    delay_seconds = serializers.IntegerField(min_value=0, default=0)
    timeout_sec = serializers.IntegerField(min_value=1, required=False)


class JobSpawnResponseSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    new_job_id = serializers.UUIDField()
    scheduled_for = serializers.DateTimeField(allow_null=True)
