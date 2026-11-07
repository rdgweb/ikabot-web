"""N-174/N-175: players marked by the organization (possible multi account, piracy target)."""
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orgs", "0006_organizationpreferences_miracle_rules"),
        ("worldintel", "0006_worlddumpcity_game_city_id_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlayerMark",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("server_id", models.CharField(db_index=True, max_length=32)),
                ("owner_id", models.CharField(blank=True, default="", max_length=32)),
                ("owner_name", models.CharField(max_length=128)),
                ("kind", models.CharField(choices=[("multi", "Possível multi conta"), ("pirate_target", "Alvo de pirataria")], max_length=16)),
                ("main_owner_name", models.CharField(blank=True, default="", max_length=128)),
                ("note", models.CharField(blank=True, default="", max_length=255)),
                ("created_by", models.CharField(blank=True, default="", max_length=150)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="player_marks", to="orgs.organization")),
            ],
            options={"ordering": ["owner_name"]},
        ),
        migrations.AddConstraint(
            model_name="playermark",
            constraint=models.UniqueConstraint(fields=("organization", "server_id", "owner_name", "kind"), name="worldintel_player_mark_unique"),
        ),
    ]
