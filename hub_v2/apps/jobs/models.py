"""
Models: Job, JobLog — execution tracking.
"""

from django.db import models

from core.mixins.models import UUIDTimestampModel


class Job(UUIDTimestampModel):
    """
    Represents a single execution of a game action.
    Created by the hub, executed by an agent runner.
    """

    STATUS_CHOICES = [
        ("queued", "Na fila"),
        ("running", "Executando"),
        ("scheduled", "Agendado"),
        ("finished", "Concluído"),
        ("error", "Erro"),
        ("cancelled", "Cancelado"),
    ]

    account = models.ForeignKey(
        "accounts.Account", on_delete=models.CASCADE, related_name="jobs",
        help_text="Lobby account (always set)",
    )
    game_account = models.ForeignKey(
        "accounts.GameAccount",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="jobs",
        help_text="Game account (set for per-server jobs like check_status)",
    )
    node = models.ForeignKey(
        "accounts.Node", on_delete=models.CASCADE, related_name="jobs"
    )
    profile = models.ForeignKey(
        "profiles.Profile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
    )

    action_code = models.IntegerField()
    source_job_id = models.UUIDField(null=True, blank=True, db_index=True)
    root_job_id = models.UUIDField(null=True, blank=True, db_index=True)
    inputs_json = models.TextField(default="{}")
    timeout_sec = models.IntegerField(default=1800)

    status = models.CharField(max_length=24, default="queued", choices=STATUS_CHOICES)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    agent = models.CharField(max_length=128, blank=True, default="")
    exit_code = models.IntegerField(null=True, blank=True)
    progress_json = models.TextField(default="{}")

    started_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["account", "status"]),
            models.Index(fields=["action_code"]),
            models.Index(fields=["lease_expires_at"]),
            models.Index(fields=["-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"Job {self.id} [{self.status}] action={self.action_code}"


class JobLog(models.Model):
    """
    Timestamped log entry for a job execution.
    Agents report these as they execute.
    """

    LEVEL_CHOICES = [
        ("info", "Info"),
        ("debug", "Debug"),
        ("warn", "Aviso"),
        ("error", "Erro"),
    ]

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="logs")
    level = models.CharField(max_length=16, default="info", choices=LEVEL_CHOICES)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"[{self.level}] {self.message[:80]}"


class ConstructionResourceReservation(models.Model):
    STATUS_CHOICES = [
        ("active", "Ativa"),
        ("released", "Liberada"),
        ("spent", "Consumida"),
        ("cancelled", "Cancelada"),
    ]

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="construction_reservations")
    account = models.ForeignKey("accounts.Account", on_delete=models.CASCADE, related_name="construction_reservations")
    game_account = models.ForeignKey(
        "accounts.GameAccount",
        on_delete=models.CASCADE,
        related_name="construction_reservations",
    )
    city_id = models.CharField(max_length=64)
    city_name = models.CharField(max_length=120, blank=True, default="")
    resource = models.CharField(max_length=24)
    reserved_local_amount = models.BigIntegerField(default=0)
    shortfall_amount = models.BigIntegerField(default=0)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["city_name", "resource"]
        indexes = [
            models.Index(fields=["game_account", "status"]),
            models.Index(fields=["job", "status"]),
            models.Index(fields=["city_id", "resource"]),
        ]

    def __str__(self):
        return f"{self.city_name or self.city_id} {self.resource} local={self.reserved_local_amount} falta={self.shortfall_amount}"
