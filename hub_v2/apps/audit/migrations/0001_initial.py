import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """N-120: append-only audit trail."""

    initial = True

    dependencies = [
        ("orgs", "0002_default_organization"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditEvent",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("actor_label", models.CharField(blank=True, default="", max_length=150)),
                ("actor_kind", models.CharField(choices=[("user", "Pessoa"), ("agent", "Agente"), ("system", "Sistema"), ("anonymous", "Anônimo")], default="user", max_length=16)),
                ("action", models.CharField(db_index=True, max_length=64)),
                ("category", models.CharField(db_index=True, max_length=24)),
                ("target_type", models.CharField(blank=True, default="", max_length=64)),
                ("target_id", models.CharField(blank=True, default="", max_length=64)),
                ("target_label", models.CharField(blank=True, default="", max_length=200)),
                ("count", models.PositiveIntegerField(default=1)),
                ("ip", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.CharField(blank=True, default="", max_length=200)),
                ("result", models.CharField(choices=[("ok", "Feito"), ("failed", "Falhou"), ("denied", "Negado")], default="ok", max_length=16)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="audit_events", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="audit_events", to="orgs.organization")),
            ],
            options={"ordering": ["-created_at", "-id"], "verbose_name": "Atividade", "verbose_name_plural": "Atividades"},
        ),
        migrations.AddIndex(
            model_name="auditevent",
            index=models.Index(fields=["organization", "created_at"], name="audit_org_created_idx"),
        ),
    ]
