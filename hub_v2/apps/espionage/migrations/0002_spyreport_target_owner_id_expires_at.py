from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("espionage", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="spyreport",
            name="target_owner_id",
            field=models.CharField(blank=True, default="", db_index=True, max_length=32),
        ),
        migrations.AddField(
            model_name="spyreport",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True, db_index=True),
        ),
        migrations.AlterField(
            model_name="spyreport",
            name="target_city_id",
            field=models.CharField(blank=True, default="", db_index=True, max_length=32),
        ),
        migrations.AddIndex(
            model_name="spyreport",
            index=models.Index(
                fields=["target_city_id", "mission_id", "-created_at"],
                name="espionage_city_mission_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="spyreport",
            index=models.Index(
                fields=["target_owner_id", "-created_at"],
                name="espionage_owner_idx",
            ),
        ),
    ]
