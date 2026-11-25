from django.conf import settings
from django.db import models
from core.mixins.models import UUIDTimestampModel


class UserProfile(UUIDTimestampModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="hub_profile")
    display_name = models.CharField(max_length=80, blank=True)
    avatar = models.BinaryField(blank=True, null=True, editable=False)
    avatar_version = models.PositiveIntegerField(default=0)
    verified_email = models.EmailField(blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    email_generation = models.PositiveIntegerField(default=0)
    last_email_sent_at = models.DateTimeField(null=True, blank=True)
    signup_organization = models.CharField(max_length=120, blank=True)
    signup_pending = models.BooleanField(default=False)
    signup_language = models.CharField(max_length=5, default="pt")
    compact_tables = models.BooleanField(default=False)
    reduced_motion = models.BooleanField(default=False)

    def email_is_verified(self):
        return bool(self.email_verified_at and self.verified_email and self.verified_email.casefold() == self.user.email.strip().casefold())


class UserSession(UUIDTimestampModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="hub_sessions")
    # Never exposed to templates, URLs or audit details. Public actions use the UUID primary key.
    session_key = models.CharField(max_length=40, unique=True)
    device = models.CharField(max_length=120, default="Navegador não identificado")
    ip = models.GenericIPAddressField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=32, blank=True)
    discovered = models.BooleanField(default=False)

    class Meta:
        ordering = ["-last_seen_at", "-created_at"]
        indexes = [models.Index(fields=["user", "revoked_at", "expires_at"], name="user_active_sessions_idx")]


class EmailChallenge(UUIDTimestampModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="email_challenge")
    target_email = models.EmailField()
    source_email = models.EmailField(blank=True)
    password_digest = models.CharField(max_length=64)
    token_digest = models.CharField(max_length=64, unique=True)
    purpose = models.CharField(max_length=10, choices=[("verify", "Verificar"), ("change", "Alterar")])
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    delivery = models.CharField(max_length=10, default="pending", choices=[("pending", "Enviando"), ("sent", "Enviado"), ("failed", "Falhou")])
