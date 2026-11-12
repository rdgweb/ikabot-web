"""Isolated test database; never connects to the installation's database or Redis."""
from .dev import *  # noqa: F403
import os

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
SESSION_ENGINE = "django.contrib.sessions.backends.db"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
TELEMETRY_DISABLED = True

# Dedicated ephemeral service in GitHub Actions; never inherit production DB_* variables.
if os.environ.get("TEST_MARIADB") == "1":
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.mysql", "NAME": "retention_ci", "USER": "root",
        "PASSWORD": "retention-ci-only", "HOST": "127.0.0.1", "PORT": "3306",
        "OPTIONS": {"charset": "utf8mb4"},
    }}
