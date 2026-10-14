import django.db.models.deletion
from django.db import migrations, models


def default_owner(apps, schema_editor):
    """The chat linked today is the default organization's."""
    Organization = apps.get_model("orgs", "Organization")
    TelegramBotConfig = apps.get_model("telegram", "TelegramBotConfig")
    organization = Organization.objects.filter(slug="principal").first()
    if organization is not None:
        TelegramBotConfig.objects.filter(pk=1, organization__isnull=True).update(organization=organization)


class Migration(migrations.Migration):
    """N-106 stage 4: one bot for the platform, one chat (and notice choices) per organization."""

    dependencies = [
        ("telegram", "0017_n109_combat_and_proxy_templates"),
        ("orgs", "0002_default_organization"),
    ]

    operations = [
        migrations.AddField(
            model_name="telegrambotconfig",
            name="organization",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                                       related_name="telegram", to="orgs.organization"),
        ),
        migrations.AlterField(model_name="telegrambotconfig", name="link_code", field=models.CharField(blank=True, default="", max_length=12)),
        migrations.AlterField(model_name="telegramaccountconfig", name="link_code", field=models.CharField(blank=True, default="", max_length=12)),
        migrations.RunPython(default_owner, migrations.RunPython.noop),
    ]
