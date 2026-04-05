"""
Initial captcha models: CaptchaConfig + CaptchaChallenge.
"""

from django.db import migrations, models
import django.db.models.deletion


def seed_config(apps, schema_editor):
    CaptchaConfig = apps.get_model("captcha", "CaptchaConfig")
    CaptchaConfig.objects.get_or_create(pk=1)


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="CaptchaConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("enabled", models.BooleanField(default=True)),
                ("solve_method", models.CharField(
                    choices=[("auto", "Automatico (ikabotapi)"), ("manual", "Manual (resolver na interface)"), ("telegram", "Assistido (via Telegram)")],
                    default="auto", max_length=16,
                )),
                ("fallback_method", models.CharField(
                    choices=[("none", "Nenhum"), ("auto", "Automatico (ikabotapi)"), ("manual", "Manual (resolver na interface)"), ("telegram", "Assistido (via Telegram)")],
                    default="manual", max_length=16,
                )),
                ("ikabotapi_url", models.CharField(blank=True, default="", max_length=512)),
                ("timeout_sec", models.IntegerField(default=120)),
            ],
            options={"verbose_name": "Configuracao de Captcha"},
        ),
        migrations.CreateModel(
            name="CaptchaChallenge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("captcha_type", models.CharField(
                    choices=[("piracy", "Pirataria"), ("drag", "Arrastar"), ("image", "Imagem"), ("lobby", "Lobby"), ("unknown", "Desconhecido")],
                    default="unknown", max_length=16,
                )),
                ("image_data", models.TextField(blank=True, default="")),
                ("challenge_id", models.CharField(blank=True, default="", max_length=128)),
                ("extra_data", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(
                    choices=[("pending", "Aguardando"), ("solving", "Resolvendo"), ("solved", "Resolvido"), ("failed", "Falhou"), ("expired", "Expirado")],
                    default="pending", max_length=16,
                )),
                ("solution", models.CharField(blank=True, default="", max_length=512)),
                ("solve_method", models.CharField(blank=True, default="", max_length=16)),
                ("error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("solved_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("game_account", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="captcha_challenges", to="accounts.gameaccount",
                )),
                ("node", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    to="accounts.node",
                )),
            ],
            options={
                "verbose_name": "Desafio Captcha",
                "verbose_name_plural": "Desafios Captcha",
                "ordering": ["-created_at"],
            },
        ),
        migrations.RunPython(seed_config, migrations.RunPython.noop),
    ]
