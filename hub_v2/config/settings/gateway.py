"""Settings of the game session gateway (N-72).

The gateway only answers on session hosts (<token>.<MANUAL_GATEWAY_DOMAIN>) and
only serves the game through the agent: no hub pages, no hub login, no admin.
"""

from .base import *  # noqa: F401,F403
from .base import MANUAL_GATEWAY_DOMAIN

ROOT_URLCONF = "config.gateway_urls"
# No sessions, no CSRF, no auth: a request is let in by the session's own cookie.
MIDDLEWARE = []
APPEND_SLASH = False
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "gateway"] + ([f".{MANUAL_GATEWAY_DOMAIN}"] if MANUAL_GATEWAY_DOMAIN else [])
# A page of the game posts forms larger than Django's default would take from an unknown client.
DATA_UPLOAD_MAX_MEMORY_SIZE = 8 * 1024 * 1024
