import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("jobs", "0001_initial"),
        ("market", "0004_internalmarketorder_target_city_id"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConstructionMarketIntervention",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(choices=[("pending", "Pendente"), ("approved", "Aprovada"), ("rejected", "Recusada"), ("sell_queued", "Venda enfileirada"), ("expired", "Expirada")], db_index=True, default="pending", max_length=24)),
                ("wait_reason", models.CharField(blank=True, default="", max_length=64)),
                ("eta_seconds", models.IntegerField(default=0)),
                ("city_id", models.IntegerField(blank=True, null=True)),
                ("city_name", models.CharField(blank=True, default="", max_length=128)),
                ("building_name", models.CharField(blank=True, default="", max_length=128)),
                ("needed_resource_idx", models.IntegerField(choices=[(0, "Madeira"), (1, "Vinho"), (2, "Mármore"), (3, "Cristal"), (4, "Enxofre")])),
                ("needed_amount", models.IntegerField(default=0)),
                ("available_gold", models.IntegerField(default=0)),
                ("min_gold", models.IntegerField(default=0)),
                ("estimated_buy_unit_min", models.IntegerField(default=0)),
                ("estimated_buy_unit_avg", models.IntegerField(default=0)),
                ("estimated_buy_cost_min", models.IntegerField(default=0)),
                ("estimated_buy_cost_avg", models.IntegerField(default=0)),
                ("sale_city_id", models.IntegerField(blank=True, null=True)),
                ("sale_city_name", models.CharField(blank=True, default="", max_length=128)),
                ("sale_branchoffice_pos", models.IntegerField(blank=True, null=True)),
                ("sale_resource_idx", models.IntegerField(choices=[(0, "Madeira"), (1, "Vinho"), (2, "Mármore"), (3, "Cristal"), (4, "Enxofre")])),
                ("sale_amount", models.IntegerField(default=0)),
                ("sale_price_min", models.IntegerField(default=0)),
                ("sale_price_max", models.IntegerField(default=0)),
                ("sale_price_target", models.IntegerField(default=0)),
                ("estimated_sale_gold", models.IntegerField(default=0)),
                ("can_fund_min", models.BooleanField(default=False)),
                ("can_fund_avg", models.BooleanField(default=False)),
                ("telegram_chat_id", models.CharField(blank=True, default="", max_length=64)),
                ("telegram_message_id", models.CharField(blank=True, default="", max_length=64)),
                ("decision_note", models.TextField(blank=True, default="")),
                ("decided_by", models.CharField(blank=True, default="", max_length=128)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="construction_market_interventions", to="accounts.account")),
                ("game_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="construction_market_interventions", to="accounts.gameaccount")),
                ("node", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="construction_market_interventions", to="accounts.node")),
                ("sell_job", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="jobs.job")),
                ("source_job", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="construction_market_interventions", to="jobs.job")),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["game_account", "status"], name="market_cons_game_ac_7e26ca_idx"),
                    models.Index(fields=["source_job", "status"], name="market_cons_source__583dff_idx"),
                ],
            },
        ),
    ]
