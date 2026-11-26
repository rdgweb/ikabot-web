from django.conf import settings
from django.db import models
from apps.orgs.scope import OrgScopedManager
from core.mixins.models import UUIDTimestampModel


class Ticket(UUIDTimestampModel):
    STATES = [('open', 'Aberto'), ('in_progress', 'Em atendimento'),
              ('waiting_customer', 'Aguardando cliente'), ('resolved', 'Resolvido')]
    CATEGORIES = [('general', 'Dúvida'), ('access', 'Acesso à conta'), ('billing', 'Plano e cobrança'),
                  ('agent', 'Agente e conexão'), ('proxy', 'Proxies'), ('game', 'Rotina do jogo'), ('bug', 'Erro no Hub')]
    organization = models.ForeignKey('orgs.Organization', on_delete=models.CASCADE)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    subject = models.CharField(max_length=180)
    category = models.CharField(max_length=20, choices=CATEGORIES)
    status = models.CharField(max_length=20, choices=STATES, default='open')
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    context_path = models.CharField(max_length=200, blank=True)
    hub_version = models.CharField(max_length=40, blank=True)
    revision = models.PositiveIntegerField(default=1)
    last_customer_at = models.DateTimeField()
    last_staff_at = models.DateTimeField(null=True, blank=True)
    objects = OrgScopedManager()

    class Meta:
        ordering = ['-updated_at', '-id']
        indexes = [models.Index(fields=['organization', 'status', 'updated_at'], name='support_org_status_idx'),
                   models.Index(fields=['status', 'updated_at'], name='support_queue_idx')]

    @property
    def code(self):
        return 'SUP-' + self.pk.hex[:10].upper()

    def __str__(self):
        return self.code


class Message(UUIDTimestampModel):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='messages')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    author_label = models.CharField(max_length=150)
    from_staff = models.BooleanField(default=False)
    body = models.TextField(max_length=8000)
    objects = OrgScopedManager()

    class Meta:
        ordering = ['created_at', 'id']
        indexes = [models.Index(fields=['ticket', 'created_at'], name='support_message_time_idx')]


class TicketEvent(UUIDTimestampModel):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    actor_label = models.CharField(max_length=150)
    description = models.CharField(max_length=300)
    objects = OrgScopedManager()

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['ticket', 'created_at'], name='support_event_time_idx')]


class Receipt(UUIDTimestampModel):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='receipts')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    seen_at = models.DateTimeField()
    objects = OrgScopedManager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['ticket', 'user'], name='support_receipt_user_uq')]
