import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """N-106 stage 4: what an organization chooses for itself."""

    dependencies = [
        ("orgs", "0002_default_organization"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrganizationPreferences",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("captcha_telegram", models.CharField(blank=True, choices=[("", "Padrão da plataforma"), ("on", "Mandar para o Telegram"), ("off", "Não mandar")], default="", max_length=8)),
                ("organization", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="preferences", to="orgs.organization")),
            ],
            options={"verbose_name": "Preferências da organização", "verbose_name_plural": "Preferências das organizações"},
        ),
    ]
