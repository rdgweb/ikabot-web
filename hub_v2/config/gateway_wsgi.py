"""WSGI entry of the game session gateway (N-72). Same code and database as the hub, its own process."""

import os

from django.core.wsgi import get_wsgi_application

os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.gateway"

application = get_wsgi_application()
