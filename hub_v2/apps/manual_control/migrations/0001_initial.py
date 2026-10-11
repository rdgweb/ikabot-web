import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """N-72: manual control of a game account (sessions and the jobs held meanwhile)."""

    initial = True

    dependencies = [
        ("accounts", "0012_gameaccount_avatar"),
        ("jobs", "0015_workflow_categories_by_subject"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ManualControlSession",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(choices=[("pausing", "Preparando"), ("active", "Controle manual"), ("resuming", "Retomando"), ("ended", "Encerrada")], db_index=True, default="pausing", max_length=16)),
                ("open_key", models.CharField(blank=True, default=None, max_length=40, null=True, unique=True)),
                ("token", models.CharField(max_length=40, unique=True)),
                ("start_key_hash", models.CharField(blank=True, default="", max_length=64)),
                ("cookie_hash", models.CharField(blank=True, default="", max_length=64)),
                ("open_request_id", models.CharField(blank=True, default="", max_length=40)),
                ("open_requested_at", models.DateTimeField(blank=True, null=True)),
                ("server_host", models.CharField(blank=True, default="", max_length=120)),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                ("last_request_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("end_reason", models.CharField(blank=True, default="", max_length=32)),
                ("error", models.TextField(blank=True, default="")),
                ("request_count", models.PositiveIntegerField(default=0)),
                ("game_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="manual_control_sessions", to="accounts.gameaccount")),
                ("node", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="manual_control_sessions", to="accounts.node")),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="manual_control_sessions", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["game_account", "status"], name="manual_ctl_ga_status_idx")],
            },
        ),
        migrations.CreateModel(
            name="HeldJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("job", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="manual_hold", to="jobs.job")),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="held_jobs", to="manual_control.manualcontrolsession")),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
    ]
