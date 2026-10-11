from django.db import migrations, models


class Migration(migrations.Migration):
    """N-98: accents in labels shown by the interface (choices, help texts, names). State only: no column changes."""

    dependencies = [
        ('jobs', '0015_workflow_categories_by_subject'),
    ]

    operations = [
        migrations.AlterField(
            model_name='job',
            name='status',
            field=models.CharField(choices=[('queued', 'Na fila'), ('running', 'Executando'), ('scheduled', 'Agendado'), ('finished', 'Concluído'), ('error', 'Erro'), ('cancelled', 'Cancelado')], default='queued', max_length=24),
        ),
        migrations.AlterField(
            model_name='workflow',
            name='status',
            field=models.CharField(choices=[('draft', 'Rascunho'), ('active', 'Ativo'), ('paused', 'Pausado'), ('waiting', 'Aguardando'), ('problem', 'Com problema'), ('finished', 'Concluído'), ('cancelled', 'Cancelado')], db_index=True, default='draft', max_length=24),
        ),
        migrations.AlterField(
            model_name='workflowrun',
            name='status',
            field=models.CharField(choices=[('scheduled', 'Agendado'), ('running', 'Executando'), ('waiting', 'Aguardando'), ('problem', 'Com problema'), ('finished', 'Concluído'), ('cancelled', 'Cancelado')], db_index=True, default='scheduled', max_length=24),
        ),
    ]
