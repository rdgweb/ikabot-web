from django.contrib import admin

from .models import DiplomacyMessage


@admin.register(DiplomacyMessage)
class DiplomacyMessageAdmin(admin.ModelAdmin):
    list_display = ["game_account", "sender", "subject", "status", "created_at"]
    list_filter = ["status", "game_account"]
    search_fields = ["sender", "subject", "body"]
    readonly_fields = ["id", "created_at", "updated_at"]
