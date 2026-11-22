"""Piracy (N-210/N-211): the month's champions and the raids that carry the capture points to them.

Every month (one piracy ranking) two accounts of the organization on a server are the champions: they convert all
their capture points into crew (the strongest crews) and raid the targets added by hand. The other accounts keep their
points, converting only a share now and then, and the points are gathered towards the champions: whenever an account's
ship is free, the hub picks whom it raids — someone earlier on the way (origin -> passer -> champion) of another lobby,
that it surely beats and that it did not raid in the last 7 days. In the game each side gets a random -10%..+10% on its crew strength and the winner takes
ALL the capture points at once, so a sure win needs crew > target crew x 1.1 / 0.9. Targets added by hand (players
outside the hub) are raided too; their crew is unknown, so the win is not sure.
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
    share_percent = models.PositiveSmallIntegerField(default=10)       # the others convert this share of their points...
    share_every_hours = models.PositiveSmallIntegerField(default=24)   # ...once in this many hours
    min_win_percent = models.PositiveSmallIntegerField(default=95)     # raid only with at least this chance of winning
    min_points = models.PositiveIntegerField(default=10000)            # an account is taken from once it holds this many points
    paused = models.BooleanField(default=False)                        # paused: every account plays as if there were no plan
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


class PiracyMember(models.Model):
    """An account's place in the month: origin (only raided), passer (raids origins, raided by the champion) or champion."""

    objects = OrgScopedManager()

    ORIGIN, PASSER, CHAMPION = "origin", "passer", "champion"
    ROLE_CHOICES = [(ORIGIN, "Origem"), (PASSER, "Passagem"), (CHAMPION, "Campeã")]
    LEVEL = {ORIGIN: 0, PASSER: 1, CHAMPION: 2}

    month = models.ForeignKey(PiracyMonth, on_delete=models.CASCADE, related_name="members")
    game_account = models.ForeignKey("accounts.GameAccount", on_delete=models.CASCADE, related_name="piracy_memberships")
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    group = models.PositiveSmallIntegerField(default=1)

    class Meta:
        unique_together = [("month", "game_account")]
        ordering = ["group", "role"]


class PiracyTarget(UUIDTimestampModel):
    """A player outside the hub added by hand as a target: the organization's accounts raid it when they can."""

    objects = OrgScopedManager()

    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="piracy_targets")
    server_id = models.CharField(max_length=32)
    player_name = models.CharField(max_length=64)
    city_id = models.CharField(max_length=32, blank=True, default="")
    island_id = models.CharField(max_length=32, blank=True, default="")
    active = models.BooleanField(default=True)
    note = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        ordering = ["player_name"]
        unique_together = [("organization", "server_id", "player_name")]

    def __str__(self):
        return f"{self.player_name} ({self.server_id})"


class PiracyRoute(UUIDTimestampModel):
    """One raid decided by the hub: `raider` takes the capture points of `target` (an account of the organization) or
    of `outside` (a target added by hand)."""

    objects = OrgScopedManager()

    PLANNED, SENT, DONE, FAILED, SKIPPED = "planned", "sent", "done", "failed", "skipped"
    STATUS_CHOICES = [(PLANNED, "Planejado"), (SENT, "A caminho"), (DONE, "Feito"), (FAILED, "Falhou"), (SKIPPED, "Pulado")]

    month = models.ForeignKey(PiracyMonth, on_delete=models.CASCADE, related_name="routes")
    group = models.PositiveSmallIntegerField(default=1)
    week = models.PositiveSmallIntegerField(default=1)          # week of the month the raid happened in
    stage = models.PositiveSmallIntegerField(default=1)         # 1: passer takes; 2: champion takes; 3: a target added by hand
    raider = models.ForeignKey("accounts.GameAccount", on_delete=models.CASCADE, related_name="piracy_raids_planned")
    target = models.ForeignKey("accounts.GameAccount", on_delete=models.CASCADE, null=True, blank=True,
                               related_name="piracy_raids_suffered")
    outside = models.ForeignKey(PiracyTarget, on_delete=models.SET_NULL, null=True, blank=True, related_name="raids")
    target_points = models.IntegerField(null=True, blank=True)  # capture points the target had when the raid was decided
    raider_crew = models.IntegerField(null=True, blank=True)
    target_crew = models.IntegerField(null=True, blank=True)
    due_from = models.DateTimeField()
    due_until = models.DateTimeField()
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PLANNED, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)
    message = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["raider", "status"], name="piracy_route_raider_idx")]

    def __str__(self):
        return f"{self.raider_id} <- {self.target_id or self.outside_id} ({self.status})"

    @property
    def target_name(self) -> str:
        return self.target.name if self.target_id else (self.outside.player_name if self.outside_id else "?")
