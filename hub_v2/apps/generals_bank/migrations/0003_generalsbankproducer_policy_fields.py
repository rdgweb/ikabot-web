from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("generals_bank", "0002_generalsbankproducer_production_template"),
    ]

    operations = [
        migrations.AddField(
            model_name="generalsbankproducer",
            name="keep_net_gold_positive",
            field=models.BooleanField(
                default=True,
                help_text="Reduce or block training if projected military upkeep would make the producer's net gold/hour negative.",
            ),
        ),
        migrations.AddField(
            model_name="generalsbankproducer",
            name="sell_only_cycle_production",
            field=models.BooleanField(
                default=True,
                help_text="If enabled, the bank cycle only sells units produced above the producer's starting stock in that cycle.",
            ),
        ),
    ]
