from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0011_gameaccount_user_agent"),
    ]

    operations = [
        migrations.AddField(
            model_name="gameaccount",
            name="avatar",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Imagem da conta: caminho de uma arte do jogo (static) ou data URI pequeno enviado pelo usuario.",
            ),
        ),
    ]
