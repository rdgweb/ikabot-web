from django.contrib import admin

from .models import AccountSnapshot, AccountSnapshotHistory


@admin.register(AccountSnapshot)
class AccountSnapshotAdmin(admin.ModelAdmin):
    list_display = ("account", "source_job_id", "updated_at")
    readonly_fields = ("updated_at",)
    raw_id_fields = ("account",)


@admin.register(AccountSnapshotHistory)
class AccountSnapshotHistoryAdmin(admin.ModelAdmin):
    list_display = ("account", "captured_at", "created_at")
    list_filter = ("captured_at",)
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("account",)
