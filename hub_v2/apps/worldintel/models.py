from django.db import models

from core.mixins.models import UUIDTimestampModel


class WorldDump(UUIDTimestampModel):
    account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.CASCADE,
        related_name="world_dumps",
    )
    game_account = models.ForeignKey(
        "accounts.GameAccount",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="world_dumps",
    )
    source_job_id = models.UUIDField(blank=True, null=True, db_index=True)
    scope_mode = models.CharField(max_length=32, default="own_islands")
    title = models.CharField(max_length=255, blank=True, default="")
    filters_json = models.JSONField(default=dict, blank=True)
    STATUS_CHOICES = [
        ("in_progress", "Em andamento"),
        ("complete", "Completo"),
        ("error", "Erro"),
    ]
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="complete", db_index=True)
    island_count = models.PositiveIntegerField(default=0)
    city_count = models.PositiveIntegerField(default=0)
    player_count = models.PositiveIntegerField(default=0)
    captured_at = models.DateTimeField()

    class Meta:
        ordering = ["-captured_at"]
        indexes = [
            models.Index(fields=["game_account", "captured_at"]),
            models.Index(fields=["account", "captured_at"]),
        ]

    def __str__(self):
        return self.title or f"Dump {self.captured_at:%Y-%m-%d %H:%M}"


class WorldDumpIsland(models.Model):
    dump = models.ForeignKey(
        WorldDump,
        on_delete=models.CASCADE,
        related_name="islands",
    )
    island_id = models.CharField(max_length=32, db_index=True)
    name = models.CharField(max_length=128, blank=True, default="")
    x = models.PositiveIntegerField(default=0)
    y = models.PositiveIntegerField(default=0)
    resource_type = models.PositiveSmallIntegerField(default=0)
    resource_name = models.CharField(max_length=64, blank=True, default="")
    resource_level = models.PositiveSmallIntegerField(default=0)
    wood_level = models.PositiveSmallIntegerField(default=0)
    miracle_type = models.PositiveSmallIntegerField(default=0)
    miracle_name = models.CharField(max_length=128, blank=True, default="")
    miracle_level = models.PositiveSmallIntegerField(default=0)
    city_count = models.PositiveIntegerField(default=0)
    helios_built = models.BooleanField(default=False)

    class Meta:
        ordering = ["x", "y", "name"]
        indexes = [
            models.Index(fields=["dump", "x", "y"]),
            models.Index(fields=["dump", "resource_type"]),
            models.Index(fields=["dump", "miracle_type"]),
        ]

    def __str__(self):
        return f"[{self.x}:{self.y}] {self.name}".strip()


class WorldDumpCity(models.Model):
    dump = models.ForeignKey(
        WorldDump,
        on_delete=models.CASCADE,
        related_name="cities",
    )
    island = models.ForeignKey(
        WorldDumpIsland,
        on_delete=models.CASCADE,
        related_name="cities",
    )
    position = models.PositiveSmallIntegerField(default=0)
    game_city_id = models.CharField(max_length=32, blank=True, default="")
    name = models.CharField(max_length=128, blank=True, default="")
    owner_id = models.CharField(max_length=32, blank=True, default="")
    owner_name = models.CharField(max_length=128, blank=True, default="")
    ally_id = models.CharField(max_length=32, blank=True, default="")
    ally_tag = models.CharField(max_length=32, blank=True, default="")
    level = models.PositiveIntegerField(default=0)
    type = models.CharField(max_length=32, blank=True, default="")
    state = models.CharField(max_length=32, blank=True, default="")
    in_fight = models.BooleanField(default=False)
    has_treaties = models.BooleanField(default=False)
    view_able = models.PositiveSmallIntegerField(default=0)
    infested_by_plague = models.BooleanField(default=False)
    actions_json = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["island__x", "island__y", "name"]
        indexes = [
            models.Index(fields=["dump", "owner_name"]),
            models.Index(fields=["dump", "owner_id"]),
            models.Index(fields=["dump", "ally_tag"]),
            models.Index(fields=["dump", "type"]),
            models.Index(fields=["dump", "state"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.owner_name or 'sem dono'})"


class WorldDumpPlayer(models.Model):
    """Consolidated player scores extracted from avatarScores in island view."""
    dump = models.ForeignKey(
        WorldDump,
        on_delete=models.CASCADE,
        related_name="players",
    )
    owner_id = models.CharField(max_length=32, db_index=True)
    owner_name = models.CharField(max_length=128, blank=True, default="")
    ally_tag = models.CharField(max_length=32, blank=True, default="")
    ally_id = models.CharField(max_length=32, blank=True, default="")
    world_rank = models.PositiveIntegerField(default=0)
    building_score = models.BigIntegerField(default=0)
    research_score = models.BigIntegerField(default=0)
    army_score = models.BigIntegerField(default=0)
    trader_score = models.BigIntegerField(default=0)
    city_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["world_rank"]
        indexes = [
            models.Index(fields=["dump", "owner_id"]),
            models.Index(fields=["dump", "ally_tag"]),
            models.Index(fields=["dump", "world_rank"]),
            models.Index(fields=["dump", "building_score"]),
        ]
        unique_together = [["dump", "owner_id"]]

    def __str__(self):
        return f"{self.owner_name} (rank {self.world_rank})"

    @property
    def total_score(self):
        return self.building_score + self.research_score + self.army_score
