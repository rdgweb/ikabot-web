from django.db import migrations, models


class Migration(migrations.Migration):
    """N-98: accents in labels shown by the interface (choices, help texts, names). State only: no column changes."""

    dependencies = [
        ('market', '0012_public_market_scan'),
    ]

    operations = [
        migrations.AlterField(
            model_name='publicmarketoffer',
            name='resource_idx',
            field=models.IntegerField(choices=[(0, 'Madeira'), (1, 'Vinho'), (2, 'Mármore'), (3, 'Cristal'), (4, 'Enxofre')]),
        ),
        migrations.AlterField(
            model_name='publicmarketscan',
            name='kind',
            field=models.CharField(choices=[('full', 'Varredura'), ('range_sync', 'Só alcance')], default='full', max_length=16),
        ),
    ]
