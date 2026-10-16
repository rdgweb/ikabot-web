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
    # N-141: the miracles activated at the right moment (apps.game.services.miracle_rules); empty = the defaults
    miracle_rules = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Preferências da organização"
        verbose_name_plural = "Preferências das organizações"

    def __str__(self):
        return f"Preferências de {self.organization}"


class Invite(UUIDTimestampModel):
    """An invitation to the beta (N-106): one use, expires; the link's token is kept only as a hash."""

    email = models.EmailField()
    organization_name = models.CharField(max_length=120)
    token_hash = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    used_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    organization = models.ForeignKey(Organization, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    # N-126: with a role, the person joins `organization` (set at creation) instead of getting their own
    role = models.CharField(max_length=16, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Convite"
        verbose_name_plural = "Convites"

    def __str__(self):
        return f"Convite {self.email}"


class TermsAcceptance(models.Model):
    """Which version of the terms a person accepted, when and from where (N-106)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="terms_acceptances")
    version = models.CharField(max_length=40)
    accepted_at = models.DateTimeField(auto_now_add=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        ordering = ["-accepted_at"]
        verbose_name = "Aceite dos termos"
        verbose_name_plural = "Aceites dos termos"
