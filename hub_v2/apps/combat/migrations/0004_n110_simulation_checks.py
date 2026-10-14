import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """N-110: the simulator checked against real combats, and what it learned from them.

    Two new tables; nothing that exists is changed.
    """

    dependencies = [("combat", "0003_n109_report_reading")]

    operations = [
        migrations.CreateModel(
            name="SimulatorCalibration",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("proposed_json", models.JSONField(blank=True, default=dict)),
                ("approved_json", models.JSONField(blank=True, default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "Ajuste do simulador", "verbose_name_plural": "Ajuste do simulador"},
        ),
        migrations.CreateModel(
            name="SimulationCheck",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("inputs_json", models.JSONField(blank=True, default=dict)),
                ("real_json", models.JSONField(blank=True, default=dict)),
                ("predicted_json", models.JSONField(blank=True, default=dict)),
                ("winner_ok", models.BooleanField(db_index=True, default=False)),
                ("rounds_gap", models.SmallIntegerField(default=0)),
                ("attacker_error", models.FloatField(default=0)),
                ("defender_error", models.FloatField(default=0)),
                ("report", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="simulation", to="combat.combatreport")),
                ("source", models.CharField(db_index=True, default="jogo", max_length=16)),
                ("external_id", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("title", models.CharField(blank=True, default="", max_length=200)),
                ("fought_at", models.CharField(blank=True, default="", max_length=32)),
            ],
            options={"verbose_name": "Conferência do simulador", "verbose_name_plural": "Conferências do simulador", "ordering": ["-created_at"]},
        ),
    ]
