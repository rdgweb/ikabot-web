from django.db import migrations, models


class Migration(migrations.Migration):
    """N-98: accents in labels shown by the interface (choices, help texts, names). State only: no column changes."""

    dependencies = [
        ('captcha', '0001_initial'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='captchaconfig',
            options={'verbose_name': 'Configuração de Captcha'},
        ),
        migrations.AlterField(
            model_name='captchachallenge',
            name='solve_method',
            field=models.CharField(blank=True, default='', help_text='Método que resolveu: auto, manual, telegram.', max_length=16),
        ),
        migrations.AlterField(
            model_name='captchaconfig',
            name='fallback_method',
            field=models.CharField(choices=[('none', 'Nenhum'), ('auto', 'Automático (ikabotapi)'), ('manual', 'Manual (resolver na interface)'), ('telegram', 'Assistido (via Telegram)')], default='manual', help_text='Método alternativo se o principal falhar.', max_length=16),
        ),
        migrations.AlterField(
            model_name='captchaconfig',
            name='solve_method',
            field=models.CharField(choices=[('auto', 'Automático (ikabotapi)'), ('manual', 'Manual (resolver na interface)'), ('telegram', 'Assistido (via Telegram)')], default='auto', help_text='Método principal de resolução.', max_length=16),
        ),
        migrations.AlterField(
            model_name='captchaconfig',
            name='timeout_sec',
            field=models.IntegerField(default=120, help_text='Tempo máximo (em segundos) para resolver antes de expirar.'),
        ),
    ]
