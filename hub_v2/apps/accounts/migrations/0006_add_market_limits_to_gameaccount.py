from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_add_open_for_market_to_gameaccount"),
    ]

    operations = [
        migrations.AddField(
            model_name="gameaccount",
            name="market_min_stock",
            field=models.PositiveIntegerField(
                default=5000,
                help_text="Minimum stock per city the seller keeps before accepting to sell (units).",
            ),
        ),
        migrations.AddField(
            model_name="gameaccount",
            name="market_min_gold",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Minimum gold balance the buyer keeps before creating a buy order (0 = no limit).",
            ),
        ),
    ]
