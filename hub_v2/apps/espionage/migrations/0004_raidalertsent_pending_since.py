from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('espionage', '0003_raidalertsent'),
    ]

    operations = [
        migrations.AddField(
            model_name='raidalertsent',
            name='pending_since',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
