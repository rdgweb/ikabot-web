"""
Models: CaptchaConfig, CaptchaChallenge — captcha management system.
"""

from django.db import models

from apps.orgs.scope import OrgScopedManager
from django.utils import timezone

from core.mixins.models import TimestampModel


class CaptchaConfig(TimestampModel):
    """
    Singleton configuration for captcha resolution strategy.

    Only one instance (pk=1) via get_or_create.
    """

    SOLVE_METHODS = [
        ("auto", "Automático (ikabotapi)"),
        ("manual", "Manual (resolver na interface)"),
        ("telegram", "Assistido (via Telegram)"),
    ]

    enabled = models.BooleanField(default=True)
    solve_method = models.CharField(
        max_length=16,
        choices=SOLVE_METHODS,
        default="auto",
        help_text="Método principal de resolução.",
    )
    fallback_method = models.CharField(
        max_length=16,
        choices=[("none", "Nenhum")] + SOLVE_METHODS,
        default="manual",
        help_text="Método alternativo se o principal falhar.",
    )
    ikabotapi_url = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="URL do servico ikabotapi (ex: http://ikabotapi:5100).",
    )
    timeout_sec = models.IntegerField(
        default=120,
        help_text="Tempo máximo (em segundos) para resolver antes de expirar.",
    )

    class Meta:
        verbose_name = "Configuração de Captcha"

    def __str__(self):
        return f"Captcha ({self.get_solve_method_display()})"

    @property
    def is_active(self):
        return self.enabled


class CaptchaChallenge(models.Model):
    """
    Tracks a single captcha challenge from detection to resolution.

    Created when an agent encounters a captcha. Status progresses:
    pending → solving → solved/failed/expired
    """
    objects = OrgScopedManager()  # N-106: only the organization of whoever is logged in

    STATUS_CHOICES = [
        ("pending", "Aguardando"),
        ("solving", "Resolvendo"),
        ("solved", "Resolvido"),
        ("failed", "Falhou"),
        ("expired", "Expirado"),
        ("not_needed", "Apareceu sem bloquear"),    # N-164: the game showed one, the action went through anyway
    ]

    TYPE_CHOICES = [
        ("piracy", "Pirataria"),
        ("abandon", "Abandono"),
        ("drag", "Arrastar"),
        ("image", "Imagem"),
        ("lobby", "Lobby"),
        ("unknown", "Desconhecido"),
    ]

    game_account = models.ForeignKey(
        "accounts.GameAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="captcha_challenges",
    )
    node = models.ForeignKey(
        "accounts.Node",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    captcha_type = models.CharField(max_length=16, choices=TYPE_CHOICES, default="unknown")
    image_data = models.TextField(
        blank=True,
        default="",
        help_text="Imagem do captcha em base64 ou URL.",
    )
    challenge_id = models.CharField(max_length=128, blank=True, default="")
    extra_data = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    solution = models.CharField(max_length=512, blank=True, default="")
    solve_method = models.CharField(
        max_length=16,
        blank=True,
        default="",
        help_text="Método que resolveu: auto, manual, telegram.",
    )
    error = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    solved_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Desafio Captcha"
        verbose_name_plural = "Desafios Captcha"

    def __str__(self):
        return f"Captcha {self.pk} ({self.captcha_type}, {self.status})"

    @property
    def is_expired(self):
        if self.expires_at and timezone.now() > self.expires_at:
            return True
        return False

    @property
    def is_pending(self):
        return self.status in ("pending", "solving") and not self.is_expired

    def mark_solved(self, solution, method=""):
        self.solution = solution
        self.status = "solved"
        self.solve_method = method
        self.solved_at = timezone.now()
        self.save(update_fields=["solution", "status", "solve_method", "solved_at"])

    def mark_failed(self, error=""):
        self.status = "failed"
        self.error = error
        self.save(update_fields=["status", "error"])

    def mark_expired(self):
        self.status = "expired"
        self.save(update_fields=["status"])
