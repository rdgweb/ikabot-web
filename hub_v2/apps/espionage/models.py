"""
Models: SpyReport — relatórios de espionagem capturados pelo agente.
"""

from django.db import models
from django.utils import timezone

from core.mixins.models import UUIDTimestampModel


class SpyReport(UUIDTimestampModel):
    """
    Relatório de espionagem capturado pelo runner SpyRunner.

    Salva todos os relatórios da Casa Segura para consulta no hub.
    Vinculado ao jogador alvo via target_owner_id para uso no runner de ataque.
    """

    game_account = models.ForeignKey(
        "accounts.GameAccount",
        on_delete=models.SET_NULL,
        null=True,
        related_name="spy_reports",
    )

    # Identificador do relatório no jogo
    report_id = models.CharField(max_length=32, unique=True)

    # Cidade de origem (com Casa Segura)
    source_city_id = models.CharField(max_length=32, blank=True, default="")

    # Cidade alvo
    target_city_id = models.CharField(max_length=32, blank=True, default="", db_index=True)
    target_city_name = models.CharField(max_length=128, blank=True, default="")
    target_x = models.IntegerField(null=True, blank=True)
    target_y = models.IntegerField(null=True, blank=True)
    target_owner = models.CharField(max_length=128, blank=True, default="")
    target_owner_id = models.CharField(max_length=32, blank=True, default="", db_index=True)

    # Missão
    mission_id = models.IntegerField(null=True, blank=True)
    mission_name = models.CharField(max_length=128, blank=True, default="")
    subject = models.CharField(max_length=256, blank=True, default="")

    # Resultado
    status = models.CharField(max_length=64, blank=True, default="")
    result_status = models.CharField(max_length=128, blank=True, default="")

    # Espiões
    agents_sent = models.IntegerField(default=0)
    agents_lost = models.IntegerField(default=0)
    decoys_sent = models.IntegerField(default=0)
    decoys_lost = models.IntegerField(default=0)

    # Conteúdo do relatório
    report_html = models.TextField(blank=True, default="")
    report_text = models.TextField(blank=True, default="")
    data_json = models.JSONField(default=dict)

    # Metadados
    date_str = models.CharField(max_length=32, blank=True, default="")
    is_read = models.BooleanField(default=False)

    # Validade — None = sem expiração definida (relatórios antigos)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # Job que coletou este relatório (link clicável no histórico)
    created_by_job = models.ForeignKey(
        "jobs.Job",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="spy_reports",
    )
    action_job = models.ForeignKey(
        "jobs.Job",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="spy_action_reports",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Relatório de Espionagem"
        verbose_name_plural = "Relatórios de Espionagem"
        indexes = [
            models.Index(fields=["target_city_id", "mission_id", "-created_at"]),
            models.Index(fields=["target_owner_id", "-created_at"]),
        ]

    def __str__(self):
        owner = self.target_owner or "?"
        city = self.target_city_name or self.target_city_id or "?"
        return f"[{self.date_str}] {owner} — {city}: {self.subject}"

    @property
    def is_valid(self) -> bool:
        """Relatório dentro do prazo de validade para decisões de ataque."""
        if self.expires_at is None:
            return False
        return self.expires_at > timezone.now()

    @property
    def is_mission_success(self) -> bool:
        """Missão executada com sucesso (normaliza PT/EN)."""
        s = (self.result_status or "").lower()
        return "success" in s or "sucesso" in s


class RaidAlertSent(UUIDTimestampModel):
    """Rastreia qual report já gerou alerta de raid por cidade.

    Comportamento:
      - last_report_id é o ID do SpyReport (mission 5) que disparou o último alerta
      - ignored_until_report_id é setado quando o usuário clica "Ignorar" no Telegram
      - Próximo alerta dispara somente se o report mais recente tem ID diferente de
        ambos os campos acima — novo report → re-alerta automaticamente
    """

    game_account = models.ForeignKey(
        "accounts.GameAccount",
        on_delete=models.CASCADE,
        related_name="raid_alerts_sent",
    )
    target_city_id = models.CharField(max_length=32, db_index=True)
    last_report_id = models.CharField(max_length=32, blank=True, default="")
    ignored_report_id = models.CharField(max_length=32, blank=True, default="")
    last_alerted_at = models.DateTimeField(null=True, blank=True)
    # Marca quando WorldSpy disparou refresh (ac=2 + ac=13) pra esse alvo.
    # Hub considera snapshots "frescos" se updated_at >= pending_since.
    pending_since = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Raid Alert Sent"
        constraints = [
            models.UniqueConstraint(
                fields=["game_account", "target_city_id"],
                name="uq_raidalert_ga_city",
            )
        ]

    def __str__(self):
        return f"RaidAlert {self.target_city_id} last={self.last_report_id} ignored={self.ignored_report_id}"
