from django.contrib import admin

from .models import SpyReport


@admin.register(SpyReport)
class SpyReportAdmin(admin.ModelAdmin):
    list_display = [
        "game_account", "target_owner", "target_city_name", "subject",
        "result_status", "agents_sent", "agents_lost", "date_str", "created_at",
    ]
    list_filter = ["game_account", "result_status", "is_read"]
    search_fields = ["target_owner", "target_city_name", "subject", "report_id"]
    readonly_fields = ["id", "created_at", "updated_at"]
