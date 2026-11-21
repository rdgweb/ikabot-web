from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """N-206: common presets of the platform, copied to every customer organization."""

    dependencies = [
        ("profiles", "0005_preset_catalog_category"),
    ]

    operations = [
        migrations.AddField(
            model_name="preset",
            name="is_common",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="preset",
            name="source",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name="copies", to="profiles.preset"),
        ),
    ]
