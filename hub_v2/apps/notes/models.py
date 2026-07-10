from django.conf import settings
from django.db import models
from django.db.models import Max

from core.mixins.models import UUIDTimestampModel


class Note(UUIDTimestampModel):
    TYPE_CHOICES = [
        ("bug", "Bug"),
        ("task", "Task"),
        ("idea", "Ideia"),
        ("note", "Nota"),
    ]
    STATUS_CHOICES = [
        ("open", "Aberta"),
        ("authorized", "Autorizada"),
        ("doing", "Em andamento"),
        ("pending_approval", "Aguardando aprovacao"),
        ("done", "Concluida"),
        ("archived", "Arquivada"),
    ]
    PRIORITY_CHOICES = [
        ("low", "Baixa"),
        ("normal", "Normal"),
        ("high", "Alta"),
        ("urgent", "Urgente"),
    ]

    sequence = models.PositiveIntegerField(null=True, blank=True, db_index=True, unique=True)
    title = models.CharField(max_length=180)
    body = models.TextField(blank=True, default="")
    note_type = models.CharField(max_length=16, choices=TYPE_CHOICES, default="task")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="open")
    priority = models.CharField(max_length=16, choices=PRIORITY_CHOICES, default="normal")
    source_url = models.URLField(blank=True, default="")
    tags = models.CharField(max_length=240, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ikabot_notes",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    claimed_by_label = models.CharField(max_length=120, blank=True, default="")
    claimed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["status", "-updated_at"]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if self.sequence is None:
            last = Note.objects.aggregate(value=Max("sequence")).get("value") or 0
            self.sequence = int(last) + 1
        super().save(*args, **kwargs)

    @property
    def code(self) -> str:
        return f"N-{self.sequence or '...'}"


class NoteEvent(UUIDTimestampModel):
    EVENT_CHOICES = [
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
    ]

    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=24, choices=EVENT_CHOICES, default="progress")
    message = models.TextField(blank=True, default="")
    actor_label = models.CharField(max_length=120, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.note.code} {self.get_event_type_display()}"


class ChangeLogEntry(UUIDTimestampModel):
    VISIBILITY_CHOICES = [
        ("dev", "Dev interno"),
        ("published", "Publicado"),
    ]
    COMPONENT_CHOICES = [
        ("hub", "Hub"),
        ("agent", "Agent"),
        ("api", "API"),
        ("infra", "Infra"),
        ("docs", "Docs"),
    ]

    component = models.CharField(max_length=16, choices=COMPONENT_CHOICES, default="hub")
    visibility = models.CharField(max_length=16, choices=VISIBILITY_CHOICES, default="dev")
    version = models.CharField(max_length=40, blank=True, default="")
    dev_version = models.CharField(max_length=40, blank=True, default="")
    published_version = models.CharField(max_length=40, blank=True, default="")
    title = models.CharField(max_length=180)
    body = models.TextField(blank=True, default="")
    note = models.ForeignKey(Note, on_delete=models.SET_NULL, null=True, blank=True, related_name="changes")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ikabot_changelog_entries",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title
