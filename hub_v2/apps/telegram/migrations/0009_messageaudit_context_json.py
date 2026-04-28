from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("telegram", "0008_incoming_commands_and_diplomacy_template"),
    ]

    operations = [
        migrations.AddField(
            model_name="messageaudit",
            name="context_json",
            field=models.TextField(blank=True, default="{}"),
        ),
    ]
