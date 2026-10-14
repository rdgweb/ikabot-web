import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """N-112: what a combat report reveals about a city's wall and a player's upgrades.

    One new table; nothing that exists is changed.
    """

    dependencies = [("combat", "0004_n110_simulation_checks")]

    operations = [
        migrations.CreateModel(
            name="BattleIntel",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("server_id", models.CharField(db_index=True, max_length=64)),
                ("kind", models.CharField(choices=[("wall", "Muralha"), ("upgrades", "Melhorias")], max_length=16)),
                ("key", models.CharField(max_length=160)),
                ("label", models.CharField(blank=True, default="", max_length=160)),
                ("value_json", models.JSONField(blank=True, default=dict)),
                ("seen_at", models.DateTimeField(blank=True, null=True)),
                ("report", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="intel", to="combat.combatreport")),
            ],
            options={"verbose_name": "Informação de combate", "verbose_name_plural": "Informações de combate", "ordering": ["-seen_at"]},
        ),
        migrations.AddConstraint(
            model_name="battleintel",
            constraint=models.UniqueConstraint(fields=("server_id", "kind", "key"), name="combat_intel_unique"),
        ),
    ]
