from django.contrib import admin
from .models import CombatReport


@admin.register(CombatReport)
class CombatReportAdmin(admin.ModelAdmin):
    list_display = ["combat_id", "combat_date", "result", "target_owner", "target_city_name", "total_loot", "total_rounds"]
    list_filter  = ["result", "combat_type", "game_account"]
    search_fields = ["target_owner", "target_city_name", "combat_id"]
    ordering = ["-combat_date"]
