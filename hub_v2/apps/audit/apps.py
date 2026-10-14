from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audit"
    verbose_name = "Registro de atividades"

    def ready(self):
        from . import signals  # noqa: F401
