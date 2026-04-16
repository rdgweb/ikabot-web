"""
Models: Node (agent server), Account (lobby account), GameAccount (sub-account/character).

Account hierarchy:
  - Account: lobby-level credentials (email + password + gf_token).
    Created manually by the user. One per Gameforge login.
  - GameAccount: individual server character discovered from the lobby.
    Auto-created by discover_characters results. Each one maps to a
    specific Ikariam server (e.g. s61-br).
"""

import secrets

from django.db import models

from core.mixins.models import UUIDTimestampModel


class Node(UUIDTimestampModel):
    """
    Represents an agent server (physical or virtual).
    Each node runs one agent_v2 instance.
    """

    name = models.CharField(max_length=64, unique=True)
    description = models.TextField(blank=True, default="")
    location = models.CharField(max_length=128, blank=True, default="")

    # Network
    proxy = models.CharField(max_length=256, blank=True, default="")
    external_ip = models.CharField(max_length=64, blank=True, default="")
    ip_source = models.CharField(max_length=64, blank=True, default="")
    ip_checked_at = models.DateTimeField(blank=True, null=True)

    # Agent info (reported via heartbeat)
    agent_name = models.CharField(max_length=128, blank=True, default="")
    agent_host = models.CharField(max_length=128, blank=True, default="")
    agent_image = models.CharField(max_length=255, blank=True, default="")
    agent_version = models.CharField(max_length=64, blank=True, default="")
    agent_last_seen_at = models.DateTimeField(blank=True, null=True)

    # Deploy token (used for agent auto-registration)
    deploy_token = models.CharField(max_length=64, blank=True, default="")

    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.deploy_token:
            self.deploy_token = f"tok_{secrets.token_hex(24)}"
        super().save(*args, **kwargs)

    @property
    def is_online(self) -> bool:
        """Agent is online if last seen within 3 minutes."""
        if not self.agent_last_seen_at:
            return False
        from django.utils import timezone

        delta = timezone.now() - self.agent_last_seen_at
        return delta.total_seconds() < 180


class Account(UUIDTimestampModel):
    """
    Lobby account — the Gameforge login.

    Holds email + encrypted password + gf_token. Created manually by the
    user. Child GameAccount records are auto-discovered.
    """

    node = models.ForeignKey(
        Node, on_delete=models.CASCADE, related_name="accounts"
    )
    label = models.CharField(
        max_length=64,
        help_text="User-friendly name for this lobby account",
    )
    email = models.CharField(max_length=255)
    password_enc = models.TextField(help_text="Fernet-encrypted password")
    gf_token_enc = models.TextField(
        blank=True,
        default="",
        help_text="Fernet-encrypted gf-token-production cookie",
    )
    notes = models.TextField(blank=True, default="")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["label"]

    def __str__(self):
        return f"{self.label} ({self.email})"


class GameAccount(UUIDTimestampModel):
    """
    Sub-account / character — a single Ikariam server within a lobby Account.

    Auto-created from discover_characters results.
    Jobs (check_status, etc.) run per GameAccount.
    """

    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="game_accounts",
    )

    # Gameforge lobby data
    lobby_account_id = models.IntegerField(
        help_text="Gameforge lobby account ID (e.g. 108637)",
    )
    server_id = models.CharField(
        max_length=32,
        help_text="Server identifier (e.g. 's61-br')",
    )
    server_language = models.CharField(
        max_length=8,
        help_text="Language code (e.g. 'br')",
    )
    server_number = models.IntegerField(
        help_text="Server number (e.g. 61)",
    )
    name = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Player/character name (e.g. 'HAVIT')",
    )
    account_group = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Account group identifier (e.g. 'br_61')",
    )

    # State
    blocked = models.BooleanField(
        default=False,
        help_text="Blocked by Gameforge (from discover_characters)",
    )
    active = models.BooleanField(
        default=True,
        help_text="User can enable/disable individual game accounts",
    )

    # World/server construction time modifier (set by game server, e.g. 10 for 10%)
    build_time_reduction = models.PositiveSmallIntegerField(
        default=0,
        help_text="World build time reduction percentage (e.g. 10 for 10%). Set per server.",
    )

    # Government type construction time reduction (e.g. 20 for Democracy -20%)
    government_time_reduction = models.PositiveSmallIntegerField(
        default=0,
        help_text="Government type build time reduction percentage (e.g. 20 for 20%). Set per character.",
    )

    # Internal market participation
    open_for_market = models.BooleanField(
        default=False,
        help_text="Account participates in the internal market as a potential seller.",
    )
    market_min_stock = models.PositiveIntegerField(
        default=5000,
        help_text="Minimum stock per city the seller keeps before accepting to sell (units).",
    )
    market_min_gold = models.PositiveIntegerField(
        default=0,
        help_text="Minimum gold balance the buyer keeps before creating a buy order (0 = no limit).",
    )

    # Cached session (persisted by agent after successful login)
    session_cookies_enc = models.TextField(
        blank=True,
        default="",
        help_text="Fernet-encrypted JSON of game session cookies",
    )
    session_updated_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="When the cached session was last saved by the agent",
    )

    class Meta:
        ordering = ["account", "server_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["account", "lobby_account_id", "server_id"],
                name="uq_gameaccount_lobby_server",
            ),
        ]
        indexes = [
            models.Index(fields=["lobby_account_id"]),
            models.Index(fields=["server_id"]),
        ]

    def __str__(self):
        return f"{self.name or self.server_id} (lobby={self.lobby_account_id})"

    @property
    def node(self):
        """Convenience: the node is inherited from the parent Account."""
        return self.account.node

    @property
    def server(self) -> str:
        """Full server hostname for backward compat."""
        return f"{self.server_id}.ikariam.gameforge.com"
