from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('worldintel', '0003_worlddumpplayer_worlddumpcity_actions_json_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='worlddump',
            name='status',
            field=models.CharField(
                choices=[('in_progress', 'Em andamento'), ('complete', 'Completo'), ('error', 'Erro')],
                db_index=True,
                default='complete',
                max_length=16,
            ),
        ),
    ]
