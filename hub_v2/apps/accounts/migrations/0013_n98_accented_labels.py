from django.db import migrations, models


class Migration(migrations.Migration):
    """N-98: accents in labels shown by the interface (choices, help texts, names). State only: no column changes."""

    dependencies = [
        ('accounts', '0012_gameaccount_avatar'),
    ]

    operations = [
        migrations.AlterField(
            model_name='agentupdaterequest',
            name='status',
            field=models.CharField(choices=[('queued', 'Na fila'), ('running', 'Atualizando'), ('succeeded', 'Concluída'), ('failed', 'Falhou'), ('cancelled', 'Cancelada')], db_index=True, default='queued', max_length=16),
        ),
        migrations.AlterField(
            model_name='gameaccount',
            name='avatar',
            field=models.TextField(blank=True, default='', help_text='Imagem da conta: caminho de uma arte do jogo (static) ou data URI pequeno enviado pelo usuário.'),
        ),
    ]
