from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("piracy", "0007_pair_days_turns")]

    operations = [
        migrations.AddField(model_name="piracyroute", name="arrives_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="piracymonth", name="turn_hour", field=models.PositiveSmallIntegerField(default=14)),
        migrations.AddField(model_name="piracymonth", name="direct_points", field=models.PositiveIntegerField(default=20000)),
    ]
