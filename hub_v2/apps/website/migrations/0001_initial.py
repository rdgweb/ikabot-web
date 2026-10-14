import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    """N-119: invitation requests sent from the public site."""

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="InviteRequest",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("email", models.EmailField(max_length=254)),
                ("name", models.CharField(blank=True, max_length=120)),
                ("accounts", models.CharField(blank=True, max_length=10)),
                ("message", models.TextField(blank=True, max_length=1000)),
                ("language", models.CharField(default="pt", max_length=5)),
                ("ip", models.GenericIPAddressField(blank=True, null=True)),
                ("handled_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["-created_at"], "verbose_name": "Pedido de convite", "verbose_name_plural": "Pedidos de convite"},
        ),
    ]
