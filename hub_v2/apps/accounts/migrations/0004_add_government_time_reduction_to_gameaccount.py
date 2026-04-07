from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_add_build_time_reduction_to_gameaccount"),
    ]

    operations = [
        migrations.AddField(
            model_name="gameaccount",
            name="government_time_reduction",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="Government type build time reduction percentage (e.g. 20 for 20%). Set per character.",
            ),
        ),
    ]
