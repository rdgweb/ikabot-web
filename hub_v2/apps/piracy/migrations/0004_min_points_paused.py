# N-211: the points that make an account worth taking from (a random time in the next day), and pausing the plan
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("piracy", "0003_min_win_percent"),
    ]

    operations = [
        migrations.AddField(model_name="piracymonth", name="min_points", field=models.PositiveIntegerField(default=10000)),
        migrations.AddField(model_name="piracymonth", name="paused", field=models.BooleanField(default=False)),
    ]
