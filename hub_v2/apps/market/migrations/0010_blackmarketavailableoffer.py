import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0007_add_chronos_level_to_gameaccount"),
        ("jobs", "0009_workflowrun_archived_at"),
        ("market", "0009_rename_bmo_ga_status_idx_market_blac_game_ac_0b1e05_idx_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="BlackMarketAvailableOffer",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("buyer_city_id", models.IntegerField()),
                ("seller_city_id", models.IntegerField()),
                ("seller_city_name", models.CharField(blank=True, default="", max_length=128)),
                ("seller_avatar", models.CharField(blank=True, default="", max_length=128)),
                ("unit_id", models.IntegerField()),
                ("unit_category", models.IntegerField(default=444)),
                ("amount", models.IntegerField(default=0)),
                ("price_per_unit", models.IntegerField(default=0)),
                ("scanned_at", models.DateTimeField(blank=True, null=True)),
                (
                    "game_account",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="bm_available_offers",
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
                "ordering": ["price_per_unit"],
                "indexes": [
                    models.Index(fields=["game_account", "buyer_city_id"], name="market_bmav_game_ac_buyc_idx"),
                    models.Index(fields=["unit_id", "unit_category"], name="market_bmav_unit_id_cat_idx"),
                ],
            },
        ),
    ]
