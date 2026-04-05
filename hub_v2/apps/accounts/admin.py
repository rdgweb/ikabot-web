from django.contrib import admin

from .models import Account, GameAccount, Node


class GameAccountInline(admin.TabularInline):
    model = GameAccount
    extra = 0
    fields = (
        "lobby_account_id", "server_id", "server_language",
        "server_number", "name", "account_group", "blocked", "active",
    )
    readonly_fields = ("lobby_account_id", "server_id", "server_language", "server_number")


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = ("name", "active", "agent_version", "is_online", "agent_last_seen_at")
    list_filter = ("active",)
    search_fields = ("name",)


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("label", "email", "node", "active", "created_at")
    list_filter = ("active", "node")
    search_fields = ("label", "email")
    inlines = [GameAccountInline]


@admin.register(GameAccount)
class GameAccountAdmin(admin.ModelAdmin):
    list_display = (
        "name", "server_id", "lobby_account_id",
        "account", "blocked", "active",
    )
    list_filter = ("active", "blocked", "server_language")
    search_fields = ("name", "server_id")
    raw_id_fields = ("account",)
