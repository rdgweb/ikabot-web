from django.conf import settings
from django.db import models
from core.mixins.models import UUIDTimestampModel


class Incident(UUIDTimestampModel):
    KINDS = [('incident', 'Incidente'), ('maintenance', 'Manutenção programada')]
    STATES = [('scheduled', 'Programada'), ('investigating', 'Em investigação'),
              ('identified', 'Causa identificada'), ('in_progress', 'Em manutenção'),
              ('monitoring', 'Em observação'), ('resolved', 'Resolvido'), ('cancelled', 'Cancelado')]
    COMPONENTS = [('hub', 'Hub'), ('agents', 'Agentes da plataforma'), ('proxy', 'Proxies da plataforma'),
                  ('jobs', 'Execução de rotinas'), ('billing', 'Compra e ativação'), ('support', 'Suporte')]
    SEVERITIES = [('info', 'Informativo'), ('degraded', 'Funcionamento parcial'), ('outage', 'Indisponibilidade')]
    title = models.CharField(max_length=180)
    kind = models.CharField(max_length=20, choices=KINDS)
    components = models.JSONField(default=list)
    severity = models.CharField(max_length=20, choices=SEVERITIES, default='degraded')
    status = models.CharField(max_length=20, choices=STATES)
    started_at = models.DateTimeField()
    scheduled_end = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    last_public_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['-last_public_at', '-created_at']
        indexes = [models.Index(fields=['published_at', 'status'], name='status_published_state_idx')]

    @property
    def component_labels(self):
        labels = dict(self.COMPONENTS)
        return [labels[c] for c in self.components if c in labels]

    @property
    def closed(self):
        return self.status in ('resolved', 'cancelled')


class Update(UUIDTimestampModel):
    incident = models.ForeignKey(Incident, on_delete=models.PROTECT, related_name='updates')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    body = models.TextField(max_length=5000)
    status = models.CharField(max_length=20, choices=Incident.STATES)
    public = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at', '-id']
