from django.conf import settings
from django.db import models
from core.mixins.models import UUIDTimestampModel


class Role(UUIDTimestampModel):
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=500, blank=True)
    scope = models.CharField(max_length=16, choices=[("platform", "Plataforma"), ("organization", "Organização")])
    organization = models.ForeignKey("orgs.Organization", null=True, blank=True, on_delete=models.CASCADE)
    permissions = models.JSONField(default=list)
    system_key = models.CharField(max_length=32, unique=True, null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class AccessMode(models.Model):
    """An explicit transition replaces legacy rights instead of silently adding to them."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    scope_key = models.CharField(max_length=36)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "scope_key"], name="uq_access_mode")]


class Grant(UUIDTimestampModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="access_grants")
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="grants")
    organization = models.ForeignKey("orgs.Organization", null=True, blank=True, on_delete=models.CASCADE)
    scope_key = models.CharField(max_length=36)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        indexes = [models.Index(fields=["user", "scope_key", "revoked_at"], name="access_user_scope_idx")]
        constraints = [models.UniqueConstraint(fields=["user", "role", "scope_key"], name="uq_access_grant")]


class AccessEvent(UUIDTimestampModel):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    actor_label = models.CharField(max_length=150)
    target_label = models.CharField(max_length=200)
    organization = models.ForeignKey("orgs.Organization", null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=50)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["organization", "created_at"], name="access_org_history_idx")]
