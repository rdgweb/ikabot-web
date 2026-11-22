# N-211: origins and passers swap roles during the month (the champions stay)
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("piracy", "0004_min_points_paused"),
    ]

    operations = [
        migrations.AddField(model_name="piracymonth", name="rotate_days", field=models.PositiveSmallIntegerField(default=7)),
    ]
