import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_add_open_for_market_to_gameaccount"),
        ("market", "0001_initial"),
    ]

    operations = [
        # Buyer game account FK
        migrations.AddField(
            model_name="internalmarketorder",
            name="buyer_game_account",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="market_orders_as_buyer_ga",
                to="accounts.gameaccount",
            ),
        ),
        # Buyer city fields (resolved at matching)
        migrations.AddField(
            model_name="internalmarketorder",
            name="buyer_city_id",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="internalmarketorder",
            name="buyer_branchoffice_pos",
            field=models.IntegerField(blank=True, null=True),
        ),
        # Seller game account FK
        migrations.AddField(
            model_name="internalmarketorder",
            name="seller_game_account",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="market_orders_as_seller_ga",
                to="accounts.gameaccount",
            ),
        ),
        # Seller city fields (resolved at matching)
        migrations.AddField(
            model_name="internalmarketorder",
            name="seller_city_id",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="internalmarketorder",
            name="seller_branchoffice_pos",
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
