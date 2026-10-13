from django.db import models

from core.mixins.models import UUIDTimestampModel


class CombatReport(UUIDTimestampModel):
    """
    Relatório de combate do jogo, entregue pelo agente (saque, verificação de ataques,
    monitor de combate).

    Guarda as duas páginas do jogo: o sumário e o relatório detalhado, round a round.
    combat_id é o ID único do jogo, usado para deduplicação.
    """

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
