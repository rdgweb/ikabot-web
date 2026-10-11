from django.db import migrations, models


class Migration(migrations.Migration):
    """N-98: accents in labels shown by the interface (choices, help texts, names). State only: no column changes."""

    dependencies = [
        ('notes', '0004_pending_approval_status'),
    ]

    operations = [
        migrations.AlterField(
            model_name='note',
            name='status',
            field=models.CharField(choices=[('open', 'Aberta'), ('authorized', 'Autorizada'), ('doing', 'Em andamento'), ('pending_approval', 'Aguardando aprovação'), ('done', 'Concluída'), ('archived', 'Arquivada')], default='open', max_length=16),
        ),
        migrations.AlterField(
            model_name='noteevent',
            name='event_type',
            field=models.CharField(choices=[('created', 'Criada'), ('authorized', 'Autorizada'), ('claimed', 'Assumida'), ('progress', 'Progresso'), ('blocked', 'Bloqueio'), ('decision', 'Decisão'), ('validated', 'Validada'), ('approval_requested', 'Aguardando aprovação'), ('approved', 'Aprovada'), ('completed', 'Concluída'), ('changelog', 'Changelog'), ('status_change', 'Status alterado')], default='progress', max_length=24),
        ),
    ]
