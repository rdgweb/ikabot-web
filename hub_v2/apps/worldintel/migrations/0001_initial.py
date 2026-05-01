import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("accounts", "0006_add_market_limits_to_gameaccount"),
    ]

    operations = [
        migrations.CreateModel(
            name="WorldDump",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("source_job_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("scope_mode", models.CharField(default="own_islands", max_length=32)),
                ("title", models.CharField(blank=True, default="", max_length=255)),
                ("filters_json", models.JSONField(blank=True, default=dict)),
                ("island_count", models.PositiveIntegerField(default=0)),
                ("city_count", models.PositiveIntegerField(default=0)),
                ("player_count", models.PositiveIntegerField(default=0)),
                ("captured_at", models.DateTimeField()),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="world_dumps", to="accounts.account")),
                ("game_account", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="world_dumps", to="accounts.gameaccount")),
            ],
            options={"ordering": ["-captured_at"]},
        ),
        migrations.CreateModel(
            name="WorldDumpIsland",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("island_id", models.CharField(db_index=True, max_length=32)),
                ("name", models.CharField(blank=True, default="", max_length=128)),
                ("x", models.PositiveIntegerField(default=0)),
                ("y", models.PositiveIntegerField(default=0)),
                ("resource_type", models.PositiveSmallIntegerField(default=0)),
                ("resource_name", models.CharField(blank=True, default="", max_length=64)),
                ("resource_level", models.CharField(blank=True, default="", max_length=32)),
                ("wood_level", models.CharField(blank=True, default="", max_length=32)),
                ("miracle_name", models.CharField(blank=True, default="", max_length=128)),
                ("miracle_level", models.CharField(blank=True, default="", max_length=32)),
                ("city_count", models.PositiveIntegerField(default=0)),
                ("dump", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="islands", to="worldintel.worlddump")),
            ],
            options={"ordering": ["x", "y", "name"]},
        ),
        migrations.CreateModel(
            name="WorldDumpCity",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("game_city_id", models.CharField(blank=True, default="", max_length=32)),
                ("name", models.CharField(blank=True, default="", max_length=128)),
                ("owner_id", models.CharField(blank=True, default="", max_length=32)),
                ("owner_name", models.CharField(blank=True, default="", max_length=128)),
                ("ally_tag", models.CharField(blank=True, default="", max_length=32)),
                ("level", models.PositiveIntegerField(default=0)),
                ("type", models.CharField(blank=True, default="", max_length=32)),
                ("state", models.CharField(blank=True, default="", max_length=32)),
                ("in_fight", models.BooleanField(default=False)),
                ("dump", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="cities", to="worldintel.worlddump")),
                ("island", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="cities", to="worldintel.worlddumpisland")),
            ],
            options={"ordering": ["island__x", "island__y", "name"]},
        ),
        migrations.AddIndex(
            model_name="worlddump",
            index=models.Index(fields=["game_account", "captured_at"], name="worldintel_dump_ga_captured_idx"),
        ),
        migrations.AddIndex(
            model_name="worlddump",
            index=models.Index(fields=["account", "captured_at"], name="worldintel_dump_acc_captured_idx"),
        ),
        migrations.AddIndex(
            model_name="worlddumpisland",
            index=models.Index(fields=["dump", "x", "y"], name="worldintel_island_dump_xy_idx"),
        ),
        migrations.AddIndex(
            model_name="worlddumpisland",
            index=models.Index(fields=["dump", "resource_type"], name="worldintel_island_dump_res_idx"),
        ),
        migrations.AddIndex(
            model_name="worlddumpcity",
            index=models.Index(fields=["dump", "owner_name"], name="worldintel_city_dump_owner_idx"),
        ),
        migrations.AddIndex(
            model_name="worlddumpcity",
            index=models.Index(fields=["dump", "owner_id"], name="worldintel_city_dump_ownerid_idx"),
        ),
        migrations.AddIndex(
            model_name="worlddumpcity",
            index=models.Index(fields=["dump", "ally_tag"], name="worldintel_city_dump_ally_idx"),
        ),
        migrations.AddIndex(
            model_name="worlddumpcity",
            index=models.Index(fields=["dump", "type"], name="worldintel_city_dump_type_idx"),
        ),
    ]
