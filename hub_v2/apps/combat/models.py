from django.db import models

from apps.orgs.scope import OrgScopedManager

from core.mixins.models import UUIDTimestampModel


class CombatReport(UUIDTimestampModel):
    """
    Relatório de combate do jogo, entregue pelo agente (saque, verificação de ataques,
    monitor de combate).

    Guarda as duas páginas do jogo: o sumário e o relatório detalhado, round a round.
    combat_id é o ID único do jogo, usado para deduplicação.
    """
    objects = OrgScopedManager()  # N-106: only the organization of whoever is logged in

    game_account = models.ForeignKey(
        "accounts.GameAccount",
        on_delete=models.SET_NULL,
        null=True,
        related_name="combat_reports",
    )

    combat_id = models.BigIntegerField(unique=True, db_index=True)

    COMBAT_TYPE_CHOICES = [("land", "Terrestre"), ("naval", "Naval")]
    combat_type = models.CharField(max_length=16, choices=COMBAT_TYPE_CHOICES, default="land")

    RESULT_CHOICES = [("victory", "Vitória"), ("defeat", "Derrota")]
    result = models.CharField(max_length=16, choices=RESULT_CHOICES, db_index=True)

    combat_date = models.DateTimeField(null=True, blank=True)
    total_rounds = models.PositiveSmallIntegerField(default=1)

    # Cidades
    source_city_id   = models.CharField(max_length=32, blank=True, db_index=True)
    source_city_name = models.CharField(max_length=128, blank=True)
    target_city_id   = models.CharField(max_length=32, blank=True, db_index=True)
    target_city_name = models.CharField(max_length=128, blank=True)
    target_owner     = models.CharField(max_length=128, blank=True)
    target_owner_id  = models.CharField(max_length=32, blank=True)

    # Resultado
    loot_json        = models.JSONField(default=dict, blank=True)  # {resource: amount}
    total_loot       = models.BigIntegerField(default=0)
    attacker_losses  = models.JSONField(default=dict, blank=True)  # {unit_id: count}
    defender_losses  = models.JSONField(default=dict, blank=True)

    # HTML dos relatórios para renderização no hub
    summary_html  = models.TextField(blank=True, default="")   # militaryAdvisorReportView
    detailed_html = models.TextField(blank=True, default="")   # militaryAdvisorDetailedReportView (todos rounds)

    # N-109: read from the pages above when the report arrives (apps.combat.services.intake)
    battle_title  = models.CharField(max_length=160, blank=True, default="")
    attacker_name = models.CharField(max_length=160, blank=True, default="")
    defender_name = models.CharField(max_length=160, blank=True, default="")
    finished      = models.BooleanField(default=False, db_index=True)   # the summary names the winners
    # what was already told on Telegram: each round and the outcome go out once
    notified_round = models.PositiveSmallIntegerField(default=0)
    notified_final = models.BooleanField(default=False)

    class Meta:
        ordering = ["-combat_date", "-created_at"]
        verbose_name = "Relatório de Combate"
        verbose_name_plural = "Relatórios de Combate"
        indexes = [
            models.Index(fields=["game_account", "-combat_date"]),
            models.Index(fields=["target_city_id", "-combat_date"]),
            models.Index(fields=["result", "-combat_date"]),
        ]

    def __str__(self):
        return f"[{self.combat_date:%d/%m %H:%M}] {self.result} vs {self.target_owner} ({self.target_city_name})"


class SimulationCheck(UUIDTimestampModel):
    """N-110: a real combat fought again in the simulator, the prediction beside what happened."""
    objects = OrgScopedManager()  # N-106: only the organization of whoever is logged in

    # a combat of the accounts has its report here; one read from a public log (IkaLogs) has none
    report = models.OneToOneField(CombatReport, on_delete=models.CASCADE, related_name="simulation", null=True, blank=True)
    source = models.CharField(max_length=16, default="jogo", db_index=True)       # "jogo" | "ikalogs"
    external_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    title = models.CharField(max_length=200, blank=True, default="")
    fought_at = models.CharField(max_length=32, blank=True, default="")           # as the game wrote it
    # what was given to the simulator (armies, field, wall, what had to be assumed) and what came out
    inputs_json = models.JSONField(default=dict, blank=True)
    real_json = models.JSONField(default=dict, blank=True)
    predicted_json = models.JSONField(default=dict, blank=True)
    winner_ok = models.BooleanField(default=False, db_index=True)
    rounds_gap = models.SmallIntegerField(default=0)            # predicted - real
    attacker_error = models.FloatField(default=0)               # 0 = the same losses, 1 = nothing alike
    defender_error = models.FloatField(default=0)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Conferência do simulador"
        verbose_name_plural = "Conferências do simulador"

    def __str__(self):
        return f"sim x real {self.report.combat_id if self.report_id else self.external_id}"


class SimulatorCalibration(models.Model):
    """N-110: what the simulator learned from the checks. One row.

    `proposed_json` is recomputed from the checks; `approved_json` is what the simulator
    actually uses, and only changes when someone approves the proposal on the screen.
    """

    proposed_json = models.JSONField(default=dict, blank=True)
    approved_json = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Ajuste do simulador"
        verbose_name_plural = "Ajuste do simulador"

    @classmethod
    def load(cls):
        row, _created = cls.objects.get_or_create(pk=1)
        return row


class BattleIntel(UUIDTimestampModel):
    """N-112: what a combat report revealed about somebody else, kept for the next simulation.

    A report shows the level of the wall it was fought against and the upgrades of every army
    in it. Before this the simulator guessed both. One row per thing known: the wall of a city
    (key = the city id) or the upgrades of a player (key = the name, lower case, no alliance tag).
    """
    objects = OrgScopedManager()  # N-106: only the organization of whoever is logged in

    KIND_CHOICES = [("wall", "Muralha"), ("upgrades", "Melhorias")]

    server_id = models.CharField(max_length=64, db_index=True)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    key = models.CharField(max_length=160)
    label = models.CharField(max_length=160, blank=True, default="")          # the city or the player as the game writes it
    value_json = models.JSONField(default=dict, blank=True)                   # {"level": 7} | {"303": [8, 6], ...}
    seen_at = models.DateTimeField(null=True, blank=True)                     # when the combat was fought
    report = models.ForeignKey(CombatReport, on_delete=models.SET_NULL, null=True, blank=True, related_name="intel")

    class Meta:
        ordering = ["-seen_at"]
        verbose_name = "Informação de combate"
        verbose_name_plural = "Informações de combate"
        constraints = [models.UniqueConstraint(fields=["server_id", "kind", "key"], name="combat_intel_unique")]

    def __str__(self):
        return f"{self.kind} {self.label or self.key}"
