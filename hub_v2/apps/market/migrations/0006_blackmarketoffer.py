from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_add_chronos_level_to_gameaccount"),
        ("jobs", "0001_initial"),
        ("market", "0005_constructionmarketintervention"),
    ]

    operations = [
        migrations.CreateModel(
            name="BlackMarketOffer",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("city_id", models.IntegerField()),
                ("city_name", models.CharField(blank=True, default="", max_length=128)),
                ("unit_id", models.IntegerField()),
                ("unit_name", models.CharField(blank=True, default="", max_length=128)),
                ("amount", models.IntegerField()),
                ("unit_price", models.IntegerField()),
                ("offer_resource", models.IntegerField(default=5)),
                ("status", models.CharField(
                    choices=[("active","Ativa"),("sold","Vendida"),("cancelled","Cancelada"),("expired","Expirada")],
                    db_index=True, default="active", max_length=24,
                )),
                ("listed_at", models.DateTimeField(blank=True, null=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("game_account", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="black_market_offers",
                    to="accounts.gameaccount",
                )),
                ("job", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to="jobs.job",
                )),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="blackmarketoffer",
            index=models.Index(fields=["game_account", "status"], name="bmo_ga_status_idx"),
        ),
        migrations.AddIndex(
            model_name="blackmarketoffer",
            index=models.Index(fields=["unit_id", "status"], name="bmo_unit_status_idx"),
        ),
        migrations.AddIndex(
            model_name="blackmarketoffer",
            index=models.Index(fields=["game_account", "city_id", "unit_id"], name="bmo_ga_city_unit_idx"),
        ),
    ]
