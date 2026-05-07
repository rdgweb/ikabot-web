"""
Models: SpyReport — relatórios de espionagem capturados pelo agente.
"""

from django.db import models

from core.mixins.models import UUIDTimestampModel


class SpyReport(UUIDTimestampModel):
    """
    Relatório de espionagem capturado pelo runner SpyRunner.

    Salva todos os relatórios da Casa Segura para consulta no hub.
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
    target_city_id = models.CharField(max_length=32, blank=True, default="")
    target_city_name = models.CharField(max_length=128, blank=True, default="")
    target_x = models.IntegerField(null=True, blank=True)
    target_y = models.IntegerField(null=True, blank=True)
    target_owner = models.CharField(max_length=128, blank=True, default="")

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

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Relatório de Espionagem"
        verbose_name_plural = "Relatórios de Espionagem"

    def __str__(self):
        owner = self.target_owner or "?"
        city = self.target_city_name or self.target_city_id or "?"
        return f"[{self.date_str}] {owner} — {city}: {self.subject}"
