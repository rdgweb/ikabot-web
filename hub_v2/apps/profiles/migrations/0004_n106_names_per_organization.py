from django.db import migrations, models


class Migration(migrations.Migration):
    """N-106 stage 3a: profile and preset names are unique per organization, not in the whole platform."""

    dependencies = [
        ("profiles", "0003_n106_organization"),
    ]

    operations = [
        migrations.AlterField(model_name="profile", name="name", field=models.CharField(max_length=96)),
        migrations.AlterField(model_name="preset", name="name", field=models.CharField(max_length=128)),
        migrations.AddConstraint(
            model_name="profile",
            constraint=models.UniqueConstraint(fields=("organization", "name"), name="uq_profile_org_name"),
        ),
        migrations.AddConstraint(
            model_name="preset",
            constraint=models.UniqueConstraint(fields=("organization", "name"), name="uq_preset_org_name"),
        ),
    ]
