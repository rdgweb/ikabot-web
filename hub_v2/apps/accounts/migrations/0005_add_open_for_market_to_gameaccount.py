from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_add_government_time_reduction_to_gameaccount"),
    ]

    operations = [
        migrations.AddField(
            model_name="gameaccount",
            name="open_for_market",
            field=models.BooleanField(
                default=False,
                help_text="Account participates in the internal market as a potential seller.",
            ),
        ),
    ]
