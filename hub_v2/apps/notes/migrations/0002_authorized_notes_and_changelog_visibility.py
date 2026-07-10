from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notes", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="note",
            name="status",
            field=models.CharField(
                choices=[
                    ("open", "Aberta"),
                    ("authorized", "Autorizada"),
                    ("doing", "Em andamento"),
                    ("done", "Concluida"),
                    ("archived", "Arquivada"),
                ],
                default="open",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="changelogentry",
            name="visibility",
            field=models.CharField(
                choices=[("dev", "Dev interno"), ("published", "Publicado")],
                default="dev",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="changelogentry",
            name="dev_version",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="changelogentry",
            name="published_version",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
    ]
