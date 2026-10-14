"""Public site (N-119): the only thing it stores is who asked for an invitation to the beta."""
from django.db import models

from core.mixins.models import UUIDTimestampModel


class InviteRequest(UUIDTimestampModel):
    email = models.EmailField()
    name = models.CharField(max_length=120, blank=True)
    accounts = models.CharField(max_length=10, blank=True)       # how many GameForge accounts, as chosen
    message = models.TextField(max_length=1000, blank=True)
    language = models.CharField(max_length=5, default="pt")
    ip = models.GenericIPAddressField(null=True, blank=True)
    handled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Pedido de convite"
        verbose_name_plural = "Pedidos de convite"

    def __str__(self):
        return self.email
