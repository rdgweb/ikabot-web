from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("generals_bank", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="generalsbankproducer",
            name="production_template",
            field=models.JSONField(
                default=dict,
                help_text='Units this producer should train per cycle. e.g. {"303": 10, "210": 5}',
            ),
        ),
    ]
