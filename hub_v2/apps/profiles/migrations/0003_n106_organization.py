import django.db.models.deletion
from django.db import migrations, models

import apps.orgs.services


def fill(apps, schema_editor):
    """Everything that exists belongs to the default organization."""
    Organization = apps.get_model("orgs", "Organization")
    organization = Organization.objects.get(slug="principal")
    for model_name in ['profile', 'preset']:
        apps.get_model("profiles", model_name).objects.filter(organization__isnull=True).update(organization=organization)


def column(related, default=None):
    kwargs = {"default": default} if default else {}
    return models.ForeignKey(
        blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name=related, to="orgs.organization", **kwargs,
    )


class Migration(migrations.Migration):
    """N-106 stage 1: organization column, filled with the default organization. Nothing filters by it yet."""

    dependencies = [
        ("profiles", "0002_preset_models"),
        ("orgs", "0002_default_organization"),
    ]

    operations = [
        migrations.AddField(model_name="profile", name="organization", field=column("profiles")),
        migrations.AddField(model_name="preset", name="organization", field=column("presets")),
        migrations.RunPython(fill, migrations.RunPython.noop),
        migrations.AlterField(model_name="profile", name="organization", field=column("profiles", apps.orgs.services.default_organization_id)),
        migrations.AlterField(model_name="preset", name="organization", field=column("presets", apps.orgs.services.default_organization_id)),
    ]
