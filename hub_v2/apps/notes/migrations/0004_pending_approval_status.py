from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("notes", "0003_note_sequence_claim_history"),
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
                    ("pending_approval", "Aguardando aprovacao"),
                    ("done", "Concluida"),
                    ("archived", "Arquivada"),
                ],
                default="open",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="noteevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("created", "Criada"),
                    ("authorized", "Autorizada"),
                    ("claimed", "Assumida"),
                    ("progress", "Progresso"),
                    ("blocked", "Bloqueio"),
                    ("decision", "Decisao"),
                    ("validated", "Validada"),
                    ("approval_requested", "Aguardando aprovacao"),
                    ("approved", "Aprovada"),
                    ("completed", "Concluida"),
                    ("changelog", "Changelog"),
                    ("status_change", "Status alterado"),
                ],
                default="progress",
                max_length=24,
            ),
        ),
    ]
