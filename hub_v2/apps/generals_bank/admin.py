from django.contrib import admin

from .models import (
    GeneralsBankConfig,
    GeneralsBankCycle,
    GeneralsBankCycleTask,
    GeneralsBankProducer,
    GeneralsBankTransaction,
)


class GeneralsBankProducerInline(admin.TabularInline):
    model = GeneralsBankProducer
    extra = 0
    fields = ["producer_game_account", "min_resource_reserves", "min_population_reserve", "is_active"]


@admin.register(GeneralsBankConfig)
class GeneralsBankConfigAdmin(admin.ModelAdmin):
    list_display = ["bank_game_account", "is_active", "auto_vacation", "min_gold_floor", "created_at"]
    inlines = [GeneralsBankProducerInline]


class GeneralsBankCycleTaskInline(admin.TabularInline):
    model = GeneralsBankCycleTask
    extra = 0
    fields = ["producer_game_account", "unit_id", "unit_name", "quantity_target", "quantity_done", "status"]
    readonly_fields = ["status"]


@admin.register(GeneralsBankCycle)
class GeneralsBankCycleAdmin(admin.ModelAdmin):
    list_display = ["bank_config", "mode", "status", "created_at", "estimated_ready_at"]
    list_filter = ["mode", "status"]
    inlines = [GeneralsBankCycleTaskInline]


@admin.register(GeneralsBankTransaction)
class GeneralsBankTransactionAdmin(admin.ModelAdmin):
    list_display = ["cycle", "direction", "unit_id", "quantity", "unit_price", "gold_delta", "status", "created_at"]
    list_filter = ["direction", "status"]
