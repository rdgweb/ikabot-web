from django.conf import settings
from django.db import migrations

DEFAULT_SLUG = "principal"


def create_default(apps, schema_editor):
    """The single-operator hub becomes one organization; every user joins it with the access it has today."""
    Organization = apps.get_model("orgs", "Organization")
    Membership = apps.get_model("orgs", "Membership")
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)

    organization, _ = Organization.objects.get_or_create(slug=DEFAULT_SLUG, defaults={"name": "Principal"})
    for user in User.objects.all():
        groups = set(user.groups.values_list("name", flat=True))
        if user.is_superuser or user.is_staff or "admin" in groups:
            role = "owner"
        elif "viewer" in groups and "operator" not in groups:
            role = "viewer"
        else:
            role = "operator"
        Membership.objects.get_or_create(organization=organization, user=user, defaults={"role": role})


class Migration(migrations.Migration):

    dependencies = [
        ("orgs", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(create_default, migrations.RunPython.noop),
    ]
