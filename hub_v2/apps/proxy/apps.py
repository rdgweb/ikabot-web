from django.apps import AppConfig


class ProxyAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.proxy"
    verbose_name = "Proxy"
