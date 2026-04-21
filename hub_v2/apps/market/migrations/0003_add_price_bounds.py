from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("market", "0002_add_game_account_and_city_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="internalmarketorder",
            name="price_max",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="internalmarketorder",
            name="price_min",
            field=models.IntegerField(default=0),
        ),
    ]
