import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SpyReport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("report_id", models.CharField(max_length=32, unique=True)),
                ("source_city_id", models.CharField(blank=True, default="", max_length=32)),
                ("target_city_id", models.CharField(blank=True, default="", max_length=32)),
                ("target_city_name", models.CharField(blank=True, default="", max_length=128)),
                ("target_x", models.IntegerField(blank=True, null=True)),
                ("target_y", models.IntegerField(blank=True, null=True)),
                ("target_owner", models.CharField(blank=True, default="", max_length=128)),
                ("mission_id", models.IntegerField(blank=True, null=True)),
                ("mission_name", models.CharField(blank=True, default="", max_length=128)),
                ("subject", models.CharField(blank=True, default="", max_length=256)),
                ("status", models.CharField(blank=True, default="", max_length=64)),
                ("result_status", models.CharField(blank=True, default="", max_length=128)),
                ("agents_sent", models.IntegerField(default=0)),
                ("agents_lost", models.IntegerField(default=0)),
                ("decoys_sent", models.IntegerField(default=0)),
                ("decoys_lost", models.IntegerField(default=0)),
                ("report_html", models.TextField(blank=True, default="")),
                ("report_text", models.TextField(blank=True, default="")),
                ("data_json", models.JSONField(default=dict)),
                ("date_str", models.CharField(blank=True, default="", max_length=32)),
                ("is_read", models.BooleanField(default=False)),
                ("game_account", models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="spy_reports",
                    to="accounts.gameaccount",
                )),
            ],
            options={
                "ordering": ["-created_at"],
                "verbose_name": "Relatório de Espionagem",
                "verbose_name_plural": "Relatórios de Espionagem",
            },
        ),
    ]
