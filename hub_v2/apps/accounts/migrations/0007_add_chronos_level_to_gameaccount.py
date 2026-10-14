from django.db import migrations, models

FIELD = dict(default=0, help_text="Chronos Forge building level for this account.")


def add_chronos_level(apps, schema_editor):
    """The column came into the first database by hand; a new database (N-107) still needs it created.

    Where it already exists only its default is set, as this migration always did there.
    """
    connection = schema_editor.connection
    table = "accounts_gameaccount"
    with connection.cursor() as cursor:
        columns = {column.name for column in connection.introspection.get_table_description(cursor, table)}
    if "chronos_level" in columns:
        if connection.vendor == "mysql":
            schema_editor.execute(f"ALTER TABLE {table} ALTER chronos_level SET DEFAULT 0")
        return
    field = models.PositiveSmallIntegerField(**FIELD)
    field.set_attributes_from_name("chronos_level")
    schema_editor.add_field(apps.get_model("accounts", "GameAccount"), field)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0006_add_market_limits_to_gameaccount"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(add_chronos_level, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="gameaccount",
                    name="chronos_level",
                    field=models.PositiveSmallIntegerField(**FIELD),
                ),
            ],
        ),
    ]
