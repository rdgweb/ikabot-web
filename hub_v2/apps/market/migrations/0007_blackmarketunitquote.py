import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0007_add_chronos_level_to_gameaccount"),
        ("jobs", "0009_workflowrun_archived_at"),
        ("market", "0006_blackmarketoffer"),
    ]

    operations = [
        migrations.CreateModel(
            name="BlackMarketUnitQuote",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("city_id", models.IntegerField()),
                ("city_name", models.CharField(blank=True, default="", max_length=128)),
                ("unit_id", models.IntegerField()),
                ("unit_name", models.CharField(blank=True, default="", max_length=128)),
                ("offer_resource", models.IntegerField(default=5)),
                ("price_min", models.IntegerField(default=0)),
                ("price_max", models.IntegerField(default=0)),
                ("available_amount", models.IntegerField(default=0)),
                ("active_offer_amount", models.IntegerField(default=0)),
                ("active_offer_price", models.IntegerField(default=0)),
                ("captured_at", models.DateTimeField(blank=True, null=True)),
                (
                    "game_account",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="black_market_unit_quotes",
                        to="accounts.gameaccount",
                    ),
                ),
                (
                    "job",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="jobs.job",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["game_account", "city_id", "offer_resource"], name="market_blac_game_ac_97d7ce_idx"),
                    models.Index(fields=["unit_id", "offer_resource"], name="market_blac_unit_id_82a879_idx"),
                ],
            },
        ),
    ]
