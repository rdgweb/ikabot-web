from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0006_add_market_limits_to_gameaccount"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # Column already exists in DB — only set DEFAULT 0, skip CREATE
            database_operations=[
                migrations.RunSQL(
                    sql="ALTER TABLE accounts_gameaccount ALTER chronos_level SET DEFAULT 0;",
                    reverse_sql="ALTER TABLE accounts_gameaccount ALTER chronos_level DROP DEFAULT;",
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="gameaccount",
                    name="chronos_level",
                    field=models.PositiveSmallIntegerField(
                        default=0,
                        help_text="Chronos Forge building level for this account.",
                    ),
                ),
            ],
        ),
    ]
