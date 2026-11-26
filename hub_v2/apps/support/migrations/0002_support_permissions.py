from django.db import migrations
from django.utils import timezone


ADDITIONS = {
    'owner': ['organization.support.view', 'organization.support.manage'],
    'operator': ['organization.support.view', 'organization.support.manage'],
    'viewer': ['organization.support.view'],
    'platform_legacy': ['platform.support.view', 'platform.support.manage'],
}


def change(apps, schema_editor, remove=False):
    Role = apps.get_model('access_control', 'Role')
    for key, added in ADDITIONS.items():
        for role in Role.objects.using(schema_editor.connection.alias).filter(system_key=key):
            permissions = set(role.permissions)
            role.permissions = sorted(permissions - set(added) if remove else permissions | set(added))
            role.version += 1
            role.updated_at = timezone.now()
            role.save(using=schema_editor.connection.alias, update_fields=['permissions', 'version', 'updated_at'])


def backwards(apps, schema_editor):
    change(apps, schema_editor, remove=True)


class Migration(migrations.Migration):
    dependencies = [('support', '0001_initial'), ('access_control', '0002_builtin_roles')]
    operations = [migrations.RunPython(change, backwards)]
