from django.contrib import admin

from .models import ChangeLogEntry, Note, NoteEvent


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "note_type", "priority", "status", "claimed_by_label", "created_by", "updated_at")
    list_filter = ("note_type", "priority", "status")
    search_fields = ("title", "body", "tags")


@admin.register(ChangeLogEntry)
class ChangeLogEntryAdmin(admin.ModelAdmin):
    list_display = ("title", "component", "version", "note", "created_by", "created_at")
    list_filter = ("component", "version")
    search_fields = ("title", "body")


@admin.register(NoteEvent)
class NoteEventAdmin(admin.ModelAdmin):
    list_display = ("note", "event_type", "actor_label", "created_at")
    list_filter = ("event_type",)
    search_fields = ("note__title", "message", "actor_label")
