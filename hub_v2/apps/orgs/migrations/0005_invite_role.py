from django.db import migrations, models


class Migration(migrations.Migration):
    """N-126: an invitation may bring the person into an existing organization, with a role."""

    dependencies = [
        ("orgs", "0004_invite_termsacceptance"),
    ]

    operations = [
        migrations.AddField(
            model_name="invite",
            name="role",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
    ]
