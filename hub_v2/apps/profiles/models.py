"""
Model: Profile — reusable automation templates.
"""

from django.db import models

from core.mixins.models import UUIDTimestampModel


class Profile(UUIDTimestampModel):
    """
    A reusable action template that can be applied to accounts.
    Stores action_code + default inputs for quick job creation.
    """

    name = models.CharField(max_length=96, unique=True)
    description = models.TextField(blank=True, default="")
    action_code = models.IntegerField()
    inputs_json = models.TextField(
        default="[]", help_text="JSON array of default input values"
    )
    timeout_sec = models.IntegerField(default=1800)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
