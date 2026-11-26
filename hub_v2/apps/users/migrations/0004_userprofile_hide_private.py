from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("users", "0003_existing_sessions")]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="hide_private",
            field=models.BooleanField(default=False),
        ),
    ]
