from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orgs", "0005_invite_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="organizationpreferences",
            name="miracle_rules",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
