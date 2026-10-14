from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor_label", "action", "target_label", "count", "ip", "result")
    list_filter = ("category", "result", "actor_kind")
    search_fields = ("actor_label", "target_label", "action")

    # append only: read, never change
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
