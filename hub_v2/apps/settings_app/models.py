"""
Model: AppSetting — global key-value configuration store.
"""

from django.db import models
from django.conf import settings
import uuid


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


class RetentionRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    policy = models.JSONField()
    planned = models.JSONField(default=dict)
    processed = models.JSONField(default=dict)
    status = models.CharField(max_length=16, default="preview")
    backup_confirmed = models.BooleanField(default=False)


class JobRetentionReceipt(models.Model):
    job = models.OneToOneField("jobs.Job", on_delete=models.CASCADE, primary_key=True)
    compacted_at = models.DateTimeField(auto_now_add=True)
