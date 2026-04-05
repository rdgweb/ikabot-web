from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"
    verbose_name = "Core"

    def ready(self):
        # Import schema extensions so drf-spectacular can register them.
        from . import schema  # noqa: F401
