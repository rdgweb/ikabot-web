"""
Models for workflow, run, and job execution tracking.
"""

from django.conf import settings
from django.db import models

from core.mixins.models import UUIDTimestampModel


class Workflow(UUIDTimestampModel):
    STATUS_CHOICES = [
        ("draft", "Rascunho"),
        ("active", "Ativo"),
        ("paused", "Pausado"),
        ("waiting", "Aguardando"),
        ("problem", "Com problema"),
        ("finished", "Concluido"),
        ("cancelled", "Cancelado"),
    ]

    account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.CASCADE,
        related_name="workflows",
    )
    game_account = models.ForeignKey(
        "accounts.GameAccount",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="workflows",
    )
    node = models.ForeignKey(
        "accounts.Node",
        on_delete=models.CASCADE,
        related_name="workflows",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_workflows",
    )
    workflow_type = models.CharField(max_length=64, db_index=True)
    category = models.CharField(max_length=32, blank=True, default="", db_index=True)
    scope_json = models.TextField(default="{}")
    config_json = models.TextField(default="{}")
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default="draft", db_index=True)
    active_run = models.ForeignKey(
        "WorkflowRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="active_for_workflows",
    )
    root_job_id = models.UUIDField(null=True, blank=True, unique=True, db_index=True)
    next_scheduled_for = models.DateTimeField(null=True, blank=True)
    last_event_at = models.DateTimeField(null=True, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_error_summary = models.TextField(blank=True, default="")
    paused_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["status", "-updated_at"]),
            models.Index(fields=["workflow_type", "status"]),
            models.Index(fields=["account", "status"]),
            models.Index(fields=["game_account", "status"]),
            models.Index(fields=["node", "status"]),
        ]

    def __str__(self):
        return f"Workflow {self.id} [{self.workflow_type}:{self.status}]"


class WorkflowRun(UUIDTimestampModel):
    STATUS_CHOICES = [
        ("scheduled", "Agendado"),
        ("running", "Executando"),
        ("waiting", "Aguardando"),
        ("problem", "Com problema"),
        ("finished", "Concluido"),
        ("cancelled", "Cancelado"),
    ]

    workflow = models.ForeignKey(
        Workflow,
        on_delete=models.CASCADE,
        related_name="runs",
    )
    sequence = models.PositiveIntegerField()
    trigger_type = models.CharField(max_length=32, blank=True, default="manual")
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default="scheduled", db_index=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    summary_json = models.TextField(default="{}")
    stats_json = models.TextField(default="{}")
    error_summary = models.TextField(blank=True, default="")
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-sequence", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["workflow", "sequence"], name="jobs_workflowrun_sequence_uniq"),
        ]
        indexes = [
            models.Index(fields=["workflow", "-sequence"]),
            models.Index(fields=["workflow", "archived_at", "-sequence"], name="jobs_run_wf_arch_seq_idx"),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"Run {self.id} [wf={self.workflow_id} seq={self.sequence} status={self.status}]"


class Job(UUIDTimestampModel):
    """
    Represents a single execution of a game action.
    Created by the hub, executed by an agent runner.
    """

    STATUS_CHOICES = [
        ("queued", "Na fila"),
        ("running", "Executando"),
        ("scheduled", "Agendado"),
        ("finished", "Concluido"),
        ("error", "Erro"),
        ("cancelled", "Cancelado"),
    ]

    account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.CASCADE,
        related_name="jobs",
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
        "accounts.Node",
        on_delete=models.CASCADE,
        related_name="jobs",
    )
    profile = models.ForeignKey(
        "profiles.Profile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
    )

    action_code = models.IntegerField()
    workflow = models.ForeignKey(
        Workflow,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
    )
    workflow_run = models.ForeignKey(
        WorkflowRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
    )
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
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["account", "status"]),
            models.Index(fields=["action_code"]),
            models.Index(fields=["lease_expires_at"]),
            models.Index(fields=["-created_at"]),
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["workflow", "status"]),
            models.Index(fields=["workflow_run", "status"]),
            models.Index(fields=["workflow", "archived_at", "-created_at"], name="jobs_job_wf_arch_created_idx"),
            models.Index(fields=["workflow", "archived_at", "status", "-created_at"], name="jobs_job_wf_arch_stat_idx"),
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
        indexes = [
            models.Index(fields=["job", "-created_at"], name="jobs_joblog_job_created_desc"),
        ]

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
