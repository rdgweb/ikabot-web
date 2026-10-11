from django.db import migrations, models


class Migration(migrations.Migration):
    """N-98: accents in labels shown by the interface (choices, help texts, names). State only: no column changes."""

    dependencies = [
        ('telegram', '0014_add_notify_proxy_change'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='notificationtemplate',
            options={'ordering': ['event_key'], 'verbose_name': 'Template de Notificação', 'verbose_name_plural': 'Templates de Notificação'},
        ),
        migrations.AlterField(
            model_name='notificationtemplate',
            name='body_template',
            field=models.TextField(blank=True, default='', help_text='Corpo da mensagem. Aceita variáveis e quebras de linha.'),
        ),
        migrations.AlterField(
            model_name='notificationtemplate',
            name='title_template',
            field=models.CharField(default='{action_name}', help_text='Título da mensagem. Aceita variáveis como {action_name}, {ga_name}, etc.', max_length=255),
        ),
        migrations.AlterField(
            model_name='telegramaccountconfig',
            name='link_status',
            field=models.CharField(choices=[('unlinked', 'Não vinculado'), ('pending', 'Aguardando confirmação'), ('linked', 'Vinculado')], default='unlinked', max_length=16),
        ),
        migrations.AlterField(
            model_name='telegrambotconfig',
            name='link_status',
            field=models.CharField(choices=[('unlinked', 'Não vinculado'), ('pending', 'Aguardando confirmação'), ('linked', 'Vinculado')], default='unlinked', max_length=16),
        ),
    ]
