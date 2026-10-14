from django.apps import AppConfig


class OrgsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.orgs"
    verbose_name = "Organizações"

    def ready(self):
        from . import signals  # noqa: F401
