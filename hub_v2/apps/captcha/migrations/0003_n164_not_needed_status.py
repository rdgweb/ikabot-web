from django.db import migrations, models


class Migration(migrations.Migration):
    """N-164: the status for a captcha the game showed without blocking the action. State only: no column changes."""

    dependencies = [
        ('captcha', '0002_n98_accented_labels'),
    ]

    operations = [
        migrations.AlterField(
            model_name='captchachallenge',
            name='status',
            field=models.CharField(choices=[('pending', 'Aguardando'), ('solving', 'Resolvendo'), ('solved', 'Resolvido'), ('failed', 'Falhou'), ('expired', 'Expirado'), ('not_needed', 'Apareceu sem bloquear')], default='pending', max_length=16),
        ),
    ]
