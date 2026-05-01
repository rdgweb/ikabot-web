from django.contrib import admin

from .models import WorldDump, WorldDumpCity, WorldDumpIsland


@admin.register(WorldDump)
class WorldDumpAdmin(admin.ModelAdmin):
    list_display = ("captured_at", "title", "game_account", "scope_mode", "island_count", "city_count", "player_count")
    search_fields = ("title", "game_account__name", "account__label")
    list_filter = ("scope_mode",)


@admin.register(WorldDumpIsland)
class WorldDumpIslandAdmin(admin.ModelAdmin):
    list_display = ("dump", "island_id", "name", "x", "y", "resource_name", "miracle_name", "city_count")
    search_fields = ("name", "island_id")


@admin.register(WorldDumpCity)
class WorldDumpCityAdmin(admin.ModelAdmin):
    list_display = ("dump", "name", "owner_name", "ally_tag", "level", "type", "in_fight")
    search_fields = ("name", "owner_name", "ally_tag")

