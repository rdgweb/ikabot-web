from django.contrib import admin

from .models import Preset, PresetAction, PresetGameAccount, Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ["name", "action_code", "enabled", "created_at"]
    list_filter = ["enabled"]
    search_fields = ["name"]


class PresetActionInline(admin.TabularInline):
    model = PresetAction
    extra = 0
    fields = ["order", "action_code", "action_name", "inputs_json", "per_account_json"]


class PresetGameAccountInline(admin.TabularInline):
    model = PresetGameAccount
    extra = 0
    autocomplete_fields = ["game_account"]
    raw_id_fields = ["game_account"]


@admin.register(Preset)
class PresetAdmin(admin.ModelAdmin):
    list_display = ["name", "account_count", "action_count", "created_by", "created_at"]
    search_fields = ["name"]
    inlines = [PresetGameAccountInline, PresetActionInline]

    def account_count(self, obj):
        return obj.preset_accounts.count()
    account_count.short_description = "Contas"

    def action_count(self, obj):
        return obj.actions.count()
    action_count.short_description = "Acoes"
