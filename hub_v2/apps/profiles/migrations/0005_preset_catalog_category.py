from django.db import migrations, models


class Migration(migrations.Migration):
    """N-142: a preset may show up in Ações do Jogo, in the category chosen for it."""

    dependencies = [
        ("profiles", "0004_n106_names_per_organization"),
    ]

    operations = [
        migrations.AddField(
            model_name="preset",
            name="catalog_category",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
