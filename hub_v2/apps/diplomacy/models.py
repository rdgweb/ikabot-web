"""
Models: DiplomacyMessage — inbox mensagens capturadas pelo agente.
"""

import json

from django.db import models

from core.mixins.models import UUIDTimestampModel


class DiplomacyMessage(UUIDTimestampModel):
    """
    Mensagem de diplomacia capturada pelo runner DiplomacyCheckRunner.

    Salva todas as mensagens do inbox (tratados, respostas, IP sharing, etc.)
    para consulta na Central de Mensagens do hub.
    """

    STATUS_CHOICES = [
        ("new", "Nova"),
        ("notified", "Notificada"),
        ("read", "Lida"),
        ("replied", "Respondida"),
        ("action_taken", "Ação tomada"),
    ]

    game_account = models.ForeignKey(
        "accounts.GameAccount",
        on_delete=models.CASCADE,
        related_name="diplomacy_messages",
    )

    # Identificador da mensagem no jogo (inteiro como string, ex: "342412")
    game_msg_id = models.CharField(max_length=32)

    sender = models.CharField(max_length=255, blank=True, default="")
    subject = models.CharField(max_length=255, blank=True, default="")
    body = models.TextField(blank=True, default="")

    # String de data como vem do jogo (ex: "15/04/26 18:32")
    game_date = models.CharField(max_length=64, blank=True, default="")

    # Parâmetros para envio de resposta
    receiver_id = models.CharField(max_length=32, blank=True, default="")
    reply_to_game_id = models.CharField(max_length=32, blank=True, default="")

    # Lista JSON de ações disponíveis: [{"msg_type": 79, "receiver_id": "100303"}, ...]
    # Vazio = mensagem comum (só permite resposta de texto)
    actions_json = models.TextField(default="[]")

    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default="new",
        db_index=True,
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["game_account", "game_msg_id"],
                name="uq_diplomacymessage_account_msgid",
            )
        ]
        verbose_name = "Mensagem de Diplomacia"
        verbose_name_plural = "Mensagens de Diplomacia"

    def __str__(self):
        return f"[{self.get_status_display()}] {self.sender}: {self.subject}"

    @property
    def actions(self) -> list:
        """Retorna a lista de ações disponíveis (aceitar/recusar tratado, etc.)."""
        try:
            return json.loads(self.actions_json) or []
        except Exception:
            return []

    @property
    def has_actions(self) -> bool:
        return bool(self.actions)
