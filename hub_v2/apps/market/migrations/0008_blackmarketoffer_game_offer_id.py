from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("market", "0007_blackmarketunitquote"),
    ]

    operations = [
        migrations.AddField(
            model_name="blackmarketoffer",
            name="game_offer_id",
            field=models.BigIntegerField(blank=True, db_index=True, null=True),
        ),
    ]
