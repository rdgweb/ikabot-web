"""
Model: AppSetting — global key-value configuration store.
"""

from django.db import models


class AppSetting(models.Model):
    """
    Key-value store for application-level configuration.
    Used for settings that admins change at runtime.
    """

    key = models.CharField(max_length=128, primary_key=True)
    value = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return f"{self.key}={self.value[:50]}"
