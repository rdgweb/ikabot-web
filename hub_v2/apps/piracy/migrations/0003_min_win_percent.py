# N-211: raid only with at least this chance of winning (each side gets -10%..+10% on its crew)
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("piracy", "0002_plan_on_the_spot"),
    ]

    operations = [
        migrations.AddField(
            model_name="piracymonth",
            name="min_win_percent",
            field=models.PositiveSmallIntegerField(default=95),
        ),
    ]
