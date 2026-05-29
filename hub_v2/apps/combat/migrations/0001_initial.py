from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="CombatReport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("game_account", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="combat_reports", to="accounts.gameaccount")),
                ("combat_id", models.BigIntegerField(db_index=True, unique=True)),
                ("combat_type", models.CharField(choices=[("land", "Terrestre"), ("naval", "Naval")], default="land", max_length=16)),
                ("result", models.CharField(choices=[("victory", "Vitória"), ("defeat", "Derrota")], db_index=True, max_length=16)),
                ("combat_date", models.DateTimeField(blank=True, null=True)),
                ("total_rounds", models.PositiveSmallIntegerField(default=1)),
                ("source_city_id", models.CharField(blank=True, db_index=True, max_length=32)),
                ("source_city_name", models.CharField(blank=True, max_length=128)),
                ("target_city_id", models.CharField(blank=True, db_index=True, max_length=32)),
                ("target_city_name", models.CharField(blank=True, max_length=128)),
                ("target_owner", models.CharField(blank=True, max_length=128)),
                ("target_owner_id", models.CharField(blank=True, max_length=32)),
                ("loot_json", models.JSONField(blank=True, default=dict)),
                ("total_loot", models.BigIntegerField(default=0)),
                ("attacker_losses", models.JSONField(blank=True, default=dict)),
                ("defender_losses", models.JSONField(blank=True, default=dict)),
                ("summary_html", models.TextField(blank=True, default="")),
                ("detailed_html", models.TextField(blank=True, default="")),
            ],
            options={
                "verbose_name": "Relatório de Combate",
                "verbose_name_plural": "Relatórios de Combate",
                "ordering": ["-combat_date", "-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="combatreport",
            index=models.Index(fields=["game_account", "-combat_date"], name="combat_report_ga_date_idx"),
        ),
        migrations.AddIndex(
            model_name="combatreport",
            index=models.Index(fields=["target_city_id", "-combat_date"], name="combat_report_target_idx"),
        ),
        migrations.AddIndex(
            model_name="combatreport",
            index=models.Index(fields=["result", "-combat_date"], name="combat_report_result_idx"),
        ),
    ]
