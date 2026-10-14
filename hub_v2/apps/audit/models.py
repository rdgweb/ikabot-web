"""Audit trail (N-120): who did what, when, from where. Append only.

The application never edits or deletes an event; the only way out is the retention purge
(purge_before), which removes whole days older than AUDIT_RETENTION_DAYS.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class AppendOnly(Exception):
    """An audit event cannot be changed or removed by the application."""


class AuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnly("O registro de atividades não pode ser alterado.")

    def delete(self):
        raise AppendOnly("O registro de atividades não pode ser apagado.")

    def purge_before(self, moment) -> int:
        """Retention only: removes the events older than moment."""
        old = self.model.objects.filter(created_at__lt=moment)
        return old._raw_delete(old.db)


class AuditEvent(models.Model):
    USER, AGENT, SYSTEM, ANONYMOUS = "user", "agent", "system", "anonymous"
    ACTOR_KINDS = [(USER, "Pessoa"), (AGENT, "Agente"), (SYSTEM, "Sistema"), (ANONYMOUS, "Anônimo")]
    OK, FAILED, DENIED = "ok", "failed", "denied"
    RESULTS = [(OK, "Feito"), (FAILED, "Falhou"), (DENIED, "Negado")]

    id = models.BigAutoField(primary_key=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    organization = models.ForeignKey("orgs.Organization", on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_events")
    # the name at the time: the event still says who it was if the user is removed
    actor_label = models.CharField(max_length=150, blank=True, default="")
    actor_kind = models.CharField(max_length=16, choices=ACTOR_KINDS, default=USER)
    action = models.CharField(max_length=64, db_index=True)
    category = models.CharField(max_length=24, db_index=True)
    target_type = models.CharField(max_length=64, blank=True, default="")
    target_id = models.CharField(max_length=64, blank=True, default="")
    target_label = models.CharField(max_length=200, blank=True, default="")
    # the same action on many rows in one request becomes one event
    count = models.PositiveIntegerField(default=1)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=200, blank=True, default="")
    result = models.CharField(max_length=16, choices=RESULTS, default=OK)
    details = models.JSONField(default=dict, blank=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["organization", "created_at"], name="audit_org_created_idx")]
        verbose_name = "Atividade"
        verbose_name_plural = "Atividades"

    def __str__(self):
        return f"{self.created_at:%d/%m %H:%M} {self.actor_label} {self.action} {self.target_label}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnly("O registro de atividades não pode ser alterado.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnly("O registro de atividades não pode ser apagado.")
