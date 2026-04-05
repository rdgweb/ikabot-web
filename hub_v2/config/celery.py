"""Celery application for hub-side job dispatch."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.base")

app = Celery("hub_v2")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
