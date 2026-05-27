"""
Serializers for the Agent game API.
"""

from rest_framework import serializers


class SnapshotUpdateSerializer(serializers.Serializer):
    """Validates an account snapshot update from the agent."""

    account_id = serializers.UUIDField(
        help_text="Lobby account UUID (backward compat, optional if game_account_id set)",
        required=False,
        allow_null=True,
    )
    game_account_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="GameAccount UUID (preferred for new agents)",
    )
    base_snapshot = serializers.JSONField(default=dict)
    cities = serializers.JSONField(default=list)
    military = serializers.JSONField(default=dict, required=False)
    source_job_id = serializers.UUIDField(required=False, allow_null=True)


class SnapshotResponseSerializer(serializers.Serializer):
    """Response shape for snapshot creation/update."""

    ok = serializers.BooleanField()
    account_id = serializers.UUIDField()
    created = serializers.BooleanField()


class CurrentSnapshotResponseSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    account_id = serializers.UUIDField()
    game_account_id = serializers.UUIDField(allow_null=True)
    updated_at = serializers.DateTimeField()
    full_snapshot_updated_at = serializers.DateTimeField(allow_null=True)
    base_snapshot = serializers.JSONField(default=dict)
    cities = serializers.JSONField(default=list)
    military = serializers.JSONField(default=dict)


class CaptchaSolveSerializer(serializers.Serializer):
    """Validates a captcha solve request."""

    type = serializers.ChoiceField(
        choices=["pirate", "lobby"],
        default="pirate",
    )
    images = serializers.JSONField(default=dict)
