"""
Abstract base models for reuse across all apps.
"""

import uuid

from django.db import models


class UUIDModel(models.Model):
    """Base model with UUID primary key."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimestampModel(models.Model):
    """Base model with created_at and updated_at timestamps."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDTimestampModel(UUIDModel, TimestampModel):
    """Combined UUID + Timestamp base model."""

    class Meta:
        abstract = True
