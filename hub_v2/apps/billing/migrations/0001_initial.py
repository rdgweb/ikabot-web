import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """N-124: plan of each organization and the Asaas notices already handled."""

    initial = True

    dependencies = [
        ("orgs", "0004_invite_termsacceptance"),
    ]

    operations = [
        migrations.CreateModel(
            name="BillingAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("trial", "Teste"), ("active", "Assinatura"), ("canceled", "Cancelada"), ("exempt", "Isenta")], default="trial", max_length=10)),
                ("limit", models.PositiveIntegerField(default=4)),
                ("price", models.DecimalField(decimal_places=2, default=0, max_digits=8)),
                ("trial_ends_at", models.DateTimeField(blank=True, null=True)),
                ("paid_until", models.DateTimeField(blank=True, null=True)),
                ("link_id", models.CharField(blank=True, default="", max_length=64)),
                ("link_url", models.URLField(blank=True, default="")),
                ("link_limit", models.PositiveIntegerField(blank=True, null=True)),
                ("customer_id", models.CharField(blank=True, default="", max_length=64)),
                ("subscription_id", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("organization", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="billing", to="orgs.organization")),
            ],
            options={"verbose_name": "Plano da organização", "verbose_name_plural": "Planos das organizações"},
        ),
        migrations.CreateModel(
            name="BillingEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_id", models.CharField(max_length=120, unique=True)),
                ("event", models.CharField(max_length=64)),
                ("data", models.JSONField(blank=True, default=dict)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("organization", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="orgs.organization")),
            ],
            options={"ordering": ["-received_at"], "verbose_name": "Aviso do Asaas", "verbose_name_plural": "Avisos do Asaas"},
        ),
    ]
