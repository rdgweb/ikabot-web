# N-211: a ranking turn inside the plan — the roles swap there, then every rotate_days from it
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("piracy", "0005_rotate_days"),
    ]

    operations = [
        migrations.AddField(model_name="piracymonth", name="turn_at", field=models.DateTimeField(blank=True, null=True)),
    ]
