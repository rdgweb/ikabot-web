from django.contrib import admin

from .models import AccountProxyReservation, ProxyProfile


@admin.register(ProxyProfile)
class ProxyProfileAdmin(admin.ModelAdmin):
    list_display = (
        "address", "port", "country_code", "provider",
        "active", "assigned_node", "last_test_status",
    )
    list_filter = ("active", "provider", "country_code", "last_test_status")
    search_fields = ("address", "country_code")
    raw_id_fields = ("assigned_node",)


@admin.register(AccountProxyReservation)
class AccountProxyReservationAdmin(admin.ModelAdmin):
    list_display = ("account", "proxy_profile", "created_at", "updated_at")
    search_fields = (
        "account__label",
        "account__email",
        "proxy_profile__address",
    )
    raw_id_fields = ("account", "proxy_profile")
