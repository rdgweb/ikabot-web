"""N-123: the fresh state of a city reaches the older dumps of the same server by its game id."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("worldintel", "0005_worlddumpcity_position"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="worlddumpcity",
            index=models.Index(fields=["game_city_id"], name="worldintel_city_gameid_idx"),
        ),
    ]
