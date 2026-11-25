from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("piracy", "0006_turn_at")]

    operations = [
        migrations.AddField(model_name="piracymonth", name="pair_days", field=models.PositiveSmallIntegerField(default=3)),
        migrations.AddField(model_name="piracymonth", name="turns", field=models.JSONField(blank=True, default=dict)),
        migrations.AlterField(model_name="piracymonth", name="share_percent", field=models.PositiveSmallIntegerField(default=3)),
    ]
