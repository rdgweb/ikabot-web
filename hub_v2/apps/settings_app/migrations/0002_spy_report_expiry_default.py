"""Data migration: insere o AppSetting spy_report_expiry_hours com valor padrão 48."""

from django.db import migrations


def insert_defaults(apps, schema_editor):
    AppSetting = apps.get_model("settings_app", "AppSetting")
    AppSetting.objects.get_or_create(
        key="spy_report_expiry_hours",
        defaults={"value": "48"},
    )


def remove_defaults(apps, schema_editor):
    AppSetting = apps.get_model("settings_app", "AppSetting")
    AppSetting.objects.filter(key="spy_report_expiry_hours").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("settings_app", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(insert_defaults, remove_defaults),
    ]
