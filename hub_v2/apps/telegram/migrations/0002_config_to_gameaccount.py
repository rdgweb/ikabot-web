"""
Move TelegramAccountConfig de Account para GameAccount.
Adiciona game_account FK ao MessageAudit.

Dados existentes de TelegramAccountConfig sao removidos (dev only).
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("telegram", "0001_initial"),
    ]

    operations = [
        # 1. Limpar registros existentes (dev — nao ha dados de producao)
        migrations.RunSQL(
            "DELETE FROM telegram_telegramaccountconfig;",
            reverse_sql=migrations.RunSQL.noop,
        ),

        # 2. Remover campo account (OneToOne para Account)
        migrations.RemoveField(
            model_name="telegramaccountconfig",
            name="account",
        ),

        # 3. Adicionar campo game_account (OneToOne para GameAccount)
        migrations.AddField(
            model_name="telegramaccountconfig",
            name="game_account",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="telegram_config",
                to="accounts.gameaccount",
            ),
            # Tabela foi limpa no passo 1, nao precisa de default
            preserve_default=False,
        ),

        # 4. Adicionar game_account FK ao MessageAudit
        migrations.AddField(
            model_name="messageaudit",
            name="game_account",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="accounts.gameaccount",
            ),
        ),
    ]
