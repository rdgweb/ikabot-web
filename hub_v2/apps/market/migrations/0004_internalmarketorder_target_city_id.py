from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("market", "0003_add_price_bounds"),
    ]

    operations = [
        migrations.AddField(
            model_name="internalmarketorder",
            name="target_city_id",
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
