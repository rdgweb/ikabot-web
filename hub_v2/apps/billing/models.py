"""Billing (N-124): one row per organization, and the Asaas notices already handled."""
from django.db import models

from apps.orgs.models import Organization


class BillingAccount(models.Model):
    TRIAL, ACTIVE, CANCELED, EXEMPT = "trial", "active", "canceled", "exempt"
    STATUS_CHOICES = [(TRIAL, "Teste"), (ACTIVE, "Assinatura"), (CANCELED, "Cancelada"), (EXEMPT, "Isenta")]

    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name="billing")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=TRIAL)
    limit = models.PositiveIntegerField(default=4)                       # active sub-accounts the plan covers
    price = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    paid_until = models.DateTimeField(null=True, blank=True)             # the next due date of a paid plan
    # Asaas: the payment link the customer opens, the customer and the subscription it created
    link_id = models.CharField(max_length=64, blank=True, default="")
    link_url = models.URLField(blank=True, default="")
    link_limit = models.PositiveIntegerField(null=True, blank=True)
    customer_id = models.CharField(max_length=64, blank=True, default="")
    subscription_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Plano da organização"
        verbose_name_plural = "Planos das organizações"

    def __str__(self):
        return f"{self.organization} · {self.get_status_display()}"


class BillingEvent(models.Model):
    """An Asaas notice, kept once by its id (they may arrive more than once). Only ids and amounts."""

    event_id = models.CharField(max_length=120, unique=True)
    event = models.CharField(max_length=64)
    organization = models.ForeignKey(Organization, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    data = models.JSONField(default=dict, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]
        verbose_name = "Aviso do Asaas"
        verbose_name_plural = "Avisos do Asaas"
