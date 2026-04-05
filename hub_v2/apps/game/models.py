"""
Models: AccountSnapshot, AccountSnapshotHistory — game state tracking.

Snapshots are per GameAccount (one per Ikariam server character).
The ``account`` FK is kept for convenience / backward compatibility.
"""

from django.db import models

from core.mixins.models import UUIDTimestampModel


class AccountSnapshot(UUIDTimestampModel):
    """
    Current game state for a game account (cities, resources, buildings).
    Updated each time a status runner executes.
    One-to-one with GameAccount.
    """

    account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.CASCADE,
        related_name="snapshots",
        help_text="Lobby account (denormalized for convenience)",
    )
    game_account = models.OneToOneField(
        "accounts.GameAccount",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="snapshot",
        help_text="Game account (per-server). New snapshots should set this.",
    )
    # Game state data stored as JSON
    base_snapshot = models.JSONField(
        default=dict, blank=True,
        help_text="Account-level stats (gold, research, etc.)",
    )
    cities = models.JSONField(
        default=dict, blank=True,
        help_text="Cities with resources, buildings",
    )
    military = models.JSONField(
        default=dict, blank=True,
        help_text="Troops and fleet data",
    )
    # Tracking
    source_job_id = models.UUIDField(blank=True, null=True)

    class Meta:
        verbose_name = "Account Snapshot"

    def __str__(self):
        if self.game_account:
            return f"Snapshot: {self.game_account}"
        return f"Snapshot: {self.account}"


class AccountSnapshotHistory(UUIDTimestampModel):
    """
    Historical snapshots for trend analysis.
    """

    account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.CASCADE,
        related_name="snapshot_history",
    )
    game_account = models.ForeignKey(
        "accounts.GameAccount",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="snapshot_history",
    )
    base_snapshot = models.JSONField(default=dict)
    cities = models.JSONField(default=dict)
    military = models.JSONField(default=dict)
    captured_at = models.DateTimeField()

    class Meta:
        ordering = ["-captured_at"]
        indexes = [
            models.Index(fields=["account", "-captured_at"]),
            models.Index(fields=["game_account", "-captured_at"]),
        ]

    def __str__(self):
        return f"History {self.game_account_id or self.account_id} @ {self.captured_at}"
