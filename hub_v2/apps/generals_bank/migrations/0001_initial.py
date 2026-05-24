import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("accounts", "0007_add_chronos_level_to_gameaccount"),
        ("jobs", "0009_workflowrun_archived_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="GeneralsBankConfig",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("buyer_city_id", models.IntegerField(blank=True, help_text="City with Branch Office used for purchasing from producers.", null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("auto_vacation", models.BooleanField(default=True, help_text="Automatically enter/exit vacation mode during cycles.")),
                ("min_gold_floor", models.IntegerField(default=10000, help_text="When gold drops below this, trigger liquidation cycle.")),
                ("notes", models.TextField(blank=True, default="")),
                ("bank_game_account", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="generals_bank_config", to="accounts.gameaccount")),
            ],
            options={"verbose_name": "Banco de Generais", "verbose_name_plural": "Bancos de Generais", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="GeneralsBankProducer",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("min_resource_reserves", models.JSONField(default=dict, help_text='Minimum resource stocks that cannot be consumed. e.g. {"wood": 500, "marble": 200}')),
                ("min_population_reserve", models.IntegerField(default=0, help_text="Population units that must remain free (not used for training).")),
                ("is_active", models.BooleanField(default=True)),
                ("bank_config", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="producers", to="generals_bank.generalsbankconfig")),
                ("producer_game_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="generals_bank_producer_roles", to="accounts.gameaccount")),
            ],
            options={"verbose_name": "Produtora", "verbose_name_plural": "Produtoras", "ordering": ["created_at"], "unique_together": {("bank_config", "producer_game_account")}},
        ),
        migrations.CreateModel(
            name="GeneralsBankCycle",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("mode", models.CharField(choices=[("accumulation", "Acumulação"), ("liquidation", "Liquidação")], default="accumulation", max_length=16)),
                ("status", models.CharField(choices=[("training", "Treinando"), ("consolidating", "Consolidando"), ("listing", "Listando no BM"), ("bank_buying", "Banco comprando"), ("bank_listing", "Banco listando para venda"), ("buyers_buying", "Compradores comprando"), ("sleeping", "Entrando em férias"), ("completed", "Concluído"), ("failed", "Falhou"), ("cancelled", "Cancelado")], db_index=True, default="training", max_length=24)),
                ("target_units", models.JSONField(default=dict, help_text='Target units to produce. e.g. {"211": 50, "315": 100}')),
                ("estimated_ready_at", models.DateTimeField(blank=True, null=True)),
                ("notes", models.TextField(blank=True, default="")),
                ("result_note", models.TextField(blank=True, default="")),
                ("bank_config", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="cycles", to="generals_bank.generalsbankconfig")),
                ("manager_job", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="jobs.job")),
                ("buy_job", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="jobs.job", help_text="Bank wake+buy job (ac=807)")),
            ],
            options={"verbose_name": "Ciclo", "verbose_name_plural": "Ciclos", "ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="generalsbankCycle",
            index=models.Index(fields=["bank_config", "status"], name="generals_ba_bank_co_31e950_idx"),
        ),
        migrations.CreateModel(
            name="GeneralsBankCycleTask",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("city_id", models.IntegerField(help_text="City where training happens.")),
                ("city_name", models.CharField(blank=True, default="", max_length=128)),
                ("bm_city_id", models.IntegerField(blank=True, help_text="City with Black Market where units are ultimately listed.", null=True)),
                ("bm_city_name", models.CharField(blank=True, default="", max_length=128)),
                ("unit_id", models.IntegerField()),
                ("unit_name", models.CharField(blank=True, default="", max_length=128)),
                ("quantity_target", models.IntegerField(default=0)),
                ("quantity_done", models.IntegerField(default=0)),
                ("unit_price", models.IntegerField(default=0, help_text="Listed price (min price from game).")),
                ("status", models.CharField(choices=[("training", "Treinando"), ("transporting", "Transportando"), ("listed", "Listado no BM"), ("sold", "Vendido"), ("failed", "Falhou"), ("cancelled", "Cancelado")], db_index=True, default="training", max_length=24)),
                ("cycle", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tasks", to="generals_bank.generalsbankCycle")),
                ("producer_game_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="generals_bank_tasks", to="accounts.gameaccount")),
                ("training_job", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="jobs.job")),
                ("transport_job", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="jobs.job")),
                ("sell_job", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="jobs.job")),
            ],
            options={"verbose_name": "Tarefa do Ciclo", "verbose_name_plural": "Tarefas do Ciclo", "ordering": ["created_at"]},
        ),
        migrations.AddIndex(
            model_name="generalsbankCycleTask",
            index=models.Index(fields=["cycle", "status"], name="generals_ba_cycle_i_049296_idx"),
        ),
        migrations.CreateModel(
            name="GeneralsBankTransaction",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("direction", models.CharField(choices=[("buy", "Compra"), ("sell", "Venda")], max_length=8)),
                ("unit_id", models.IntegerField()),
                ("unit_name", models.CharField(blank=True, default="", max_length=128)),
                ("quantity", models.IntegerField()),
                ("unit_price", models.IntegerField()),
                ("gold_delta", models.IntegerField(default=0, help_text="Positive = gold received. Negative = gold spent.")),
                ("status", models.CharField(choices=[("pending", "Pendente"), ("completed", "Concluído"), ("failed", "Falhou")], db_index=True, default="pending", max_length=16)),
                ("notes", models.TextField(blank=True, default="")),
                ("cycle", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="transactions", to="generals_bank.generalsbankCycle")),
                ("counterpart_ga", models.ForeignKey(blank=True, help_text="The producer (on buy) or buyer account (on sell).", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="generals_bank_transactions", to="accounts.gameaccount")),
                ("job", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="jobs.job")),
            ],
            options={"verbose_name": "Transação", "verbose_name_plural": "Transações", "ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="generalsbankTransaction",
            index=models.Index(fields=["cycle", "direction"], name="generals_ba_cycle_i_28fe25_idx"),
        ),
    ]
