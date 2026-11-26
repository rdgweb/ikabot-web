from django.db import migrations


PERMISSIONS = {'platform.incidents.view', 'platform.incidents.manage'}


def update(apps, schema_editor, remove=False):
    Role = apps.get_model('access_control','Role')
    for role in Role.objects.using(schema_editor.connection.alias).filter(system_key='platform_legacy'):
        role.permissions = sorted(set(role.permissions) - PERMISSIONS if remove else set(role.permissions) | PERMISSIONS)
        role.version += 1
        role.save(using=schema_editor.connection.alias,update_fields=['permissions','version','updated_at'])


def backwards(apps, schema_editor):
    update(apps,schema_editor,True)


class Migration(migrations.Migration):
    dependencies = [('service_status','0001_initial'),('support','0002_support_permissions')]
    operations = [migrations.RunPython(update,backwards)]
