"""Organizations (N-106): who owns the accounts, nodes and proxies of the hub.

Most organizations are one person: signing up creates one, and the word "organização" only shows
up when someone invites another person. Stage 1 only records the owner; nothing is filtered yet.
"""
from django.conf import settings
from django.db import models

from core.mixins.models import UUIDTimestampModel


class Organization(UUIDTimestampModel):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=60, unique=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Organização"
        verbose_name_plural = "Organizações"

    def __str__(self):
        return self.name


class Membership(UUIDTimestampModel):
    OWNER = "owner"
    OPERATOR = "operator"
    VIEWER = "viewer"
    ROLE_CHOICES = [
        (OWNER, "Dono"),          # everything, members and billing included
        (OPERATOR, "Operador"),   # runs the accounts: jobs, actions, settings of the game
        (VIEWER, "Leitor"),       # only looks
    ]

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=OPERATOR)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "user"], name="uq_membership_organization_user"),
        ]
        verbose_name = "Membro"
        verbose_name_plural = "Membros"

    def __str__(self):
        return f"{self.user} @ {self.organization} ({self.role})"


class OrganizationPreferences(UUIDTimestampModel):
    """What an organization chooses for itself (N-106, stage 4). Empty = the platform's default."""

    PLATFORM, ON, OFF = "", "on", "off"
    CAPTCHA_TELEGRAM_CHOICES = [
        (PLATFORM, "Padrão da plataforma"),
        (ON, "Mandar para o Telegram"),
        (OFF, "Não mandar"),
    ]

    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name="preferences")
    # when the automatic solver fails, send the captcha to this organization's Telegram chat
    captcha_telegram = models.CharField(max_length=8, choices=CAPTCHA_TELEGRAM_CHOICES, blank=True, default=PLATFORM)

    class Meta:
        verbose_name = "Preferências da organização"
        verbose_name_plural = "Preferências das organizações"

    def __str__(self):
        return f"Preferências de {self.organization}"
