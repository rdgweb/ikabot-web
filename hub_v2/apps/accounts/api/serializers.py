"""
Serializers for the Agent accounts API.
"""

from rest_framework import serializers


class AgentRegisterSerializer(serializers.Serializer):
    """Validates agent registration payload."""

    node_id = serializers.UUIDField()
    deploy_token = serializers.CharField(required=False, allow_blank=True, default="")
    agent_name = serializers.CharField(required=False, allow_blank=True, default="")
    agent_host = serializers.CharField(required=False, allow_blank=True, default="")
    agent_version = serializers.CharField(required=False, allow_blank=True, default="")
    agent_image = serializers.CharField(required=False, allow_blank=True, default="")


class AgentRegisterResponseSerializer(serializers.Serializer):
    """Response shape for agent registration."""

    node_id = serializers.UUIDField()
    node_name = serializers.CharField()
    proxy = serializers.CharField()
    active = serializers.BooleanField()


class AgentHeartbeatSerializer(serializers.Serializer):
    """Validates heartbeat payload."""

    node_id = serializers.UUIDField()
    external_ip = serializers.IPAddressField(required=False, allow_blank=True, default="")


# ── Config endpoint serializers ──────────────────────────────────────


class GameAccountConfigSerializer(serializers.Serializer):
    """Serializes a single game account (sub-account/character)."""

    id = serializers.UUIDField()
    lobby_account_id = serializers.IntegerField()
    server_id = serializers.CharField()
    server_language = serializers.CharField()
    server_number = serializers.IntegerField()
    server = serializers.CharField(help_text="Full server hostname")
    name = serializers.CharField(allow_blank=True)
    account_group = serializers.CharField(allow_blank=True)
    blocked = serializers.BooleanField()
    active = serializers.BooleanField()
    cached_session = serializers.CharField(
        allow_blank=True, default="",
        help_text="Decrypted JSON of cached game session cookies",
    )


class AccountConfigSerializer(serializers.Serializer):
    """Serializes a lobby account with nested game accounts for the config endpoint."""

    id = serializers.UUIDField()
    label = serializers.CharField()
    email = serializers.CharField()
    password = serializers.CharField(help_text="Decrypted password")
    gf_token = serializers.CharField(
        allow_blank=True, help_text="Decrypted gf-token-production cookie"
    )
    game_accounts = GameAccountConfigSerializer(many=True)


class NodeConfigResponseSerializer(serializers.Serializer):
    """Response shape for the config endpoint."""

    node_id = serializers.UUIDField()
    node_name = serializers.CharField()
    proxy = serializers.CharField()
    proxy_verified = serializers.BooleanField(
        help_text="True if proxy was tested and returned a valid IP. "
                  "False/null means proxy changed or was never tested.",
    )
    active = serializers.BooleanField()
    system_settings = serializers.JSONField(required=False)
    accounts = AccountConfigSerializer(many=True)
