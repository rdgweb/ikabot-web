"""Piracy (N-210/N-211): the month's champions and the routes the capture points take to reach them.

Every month (one piracy ranking) two accounts of the organization on a server are the champions; the others play
the Pirate Fortress as usual and, week by week, the points pass through other accounts before reaching a champion:
a passer raids an origin (stage 1), then the champion raids the passer (stage 2). Two fixed rules: accounts of the
same lobby (GameForge login) never raid each other, and the same raider never raids the same account twice in a week.
"""
from django.db import models

from apps.orgs.scope import OrgScopedManager
from core.mixins.models import UUIDTimestampModel


class PiracyMonth(UUIDTimestampModel):
    """One month of the piracy ranking on one server, with its plan."""

    objects = OrgScopedManager()

    OPEN, CLOSED = "open", "closed"
    STATUS_CHOICES = [(OPEN, "Em andamento"), (CLOSED, "Encerrado")]

    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="piracy_months")
    server_id = models.CharField(max_length=32)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=OPEN, db_index=True)
    chosen_by = models.CharField(max_length=12, default="sorteio")        # sorteio | escolha
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-starts_at"]

    def __str__(self):
        return f"{self.server_id} {self.starts_at:%d/%m}–{self.ends_at:%d/%m}"


class PiracyChampion(models.Model):
    """A champion of the month: the end of one group's routes."""

    objects = OrgScopedManager()

    month = models.ForeignKey(PiracyMonth, on_delete=models.CASCADE, related_name="champions")
    game_account = models.ForeignKey("accounts.GameAccount", on_delete=models.CASCADE, related_name="piracy_championships")
    group = models.PositiveSmallIntegerField(default=1)

    class Meta:
        unique_together = [("month", "game_account")]
        ordering = ["group"]


class PiracyRoute(UUIDTimestampModel):
    """One raid of the plan: `raider` takes the capture points of `target` in that week and stage."""

    objects = OrgScopedManager()

    PLANNED, SENT, DONE, FAILED, SKIPPED = "planned", "sent", "done", "failed", "skipped"
    STATUS_CHOICES = [(PLANNED, "Planejado"), (SENT, "A caminho"), (DONE, "Feito"), (FAILED, "Falhou"), (SKIPPED, "Pulado")]

    month = models.ForeignKey(PiracyMonth, on_delete=models.CASCADE, related_name="routes")
    group = models.PositiveSmallIntegerField(default=1)
    week = models.PositiveSmallIntegerField()                   # 1..n inside the month
    stage = models.PositiveSmallIntegerField()                  # 1: passer takes from an origin; 2: champion takes from a passer
    raider = models.ForeignKey("accounts.GameAccount", on_delete=models.CASCADE, related_name="piracy_raids_planned")
    target = models.ForeignKey("accounts.GameAccount", on_delete=models.CASCADE, related_name="piracy_raids_suffered")
    due_from = models.DateTimeField()
    due_until = models.DateTimeField()
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PLANNED, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)
    message = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["week", "stage", "group"]
        unique_together = [("month", "week", "raider", "target")]   # the same raider takes from the same account once a week
        indexes = [models.Index(fields=["raider", "status"], name="piracy_route_raider_idx")]

    def __str__(self):
        return f"S{self.week}.{self.stage} {self.raider_id} <- {self.target_id} ({self.status})"
