"""
Django settings — base configuration.
Shared between dev and prod environments.
"""

import os
import sys
from pathlib import Path

# DRF imports django.contrib.postgres which can hit recursion limits on MySQL setups
sys.setrecursionlimit(2000)

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Read VERSION file
VERSION = (BASE_DIR / "VERSION").read_text().strip()

# --- Security ---

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "django-insecure-CHANGE-ME-IN-PRODUCTION"
)

DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = os.environ.get(
    "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"
).split(",")

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

# --- Application definition ---

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Third-party
    "rest_framework",
    "drf_spectacular",
    "django_htmx",
    "django_filters",  # installed as django-filter
    # Core (templatetags, mixins)
    "core.apps.CoreConfig",
    # Project apps
    "apps.accounts",
    "apps.profiles",
    "apps.jobs",
    "apps.dashboard",
    "apps.game",
    "apps.worldintel",
    "apps.market",
    "apps.diplomacy",
    "apps.espionage",
    "apps.combat",
    "apps.telegram",
    "apps.proxy",
    "apps.captcha",
    "apps.settings_app",
    "apps.users",
    "apps.generals_bank",
    "apps.notes",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "core.middleware.HtmxBoostMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        # APP_DIRS=True is incompatible with explicit loaders, so we
        # replicate it manually inside the cached loader.
        "APP_DIRS": False,
        "OPTIONS": {
            "loaders": [
                (
                    "django.template.loaders.cached.Loader",
                    [
                        "django.template.loaders.filesystem.Loader",
                        "django.template.loaders.app_directories.Loader",
                    ],
                ),
            ],
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "core.context_processors.nav_context",
                "core.context_processors.hub_version",
                "core.context_processors.htmx_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database ---

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.environ.get("DB_NAME", "ikabot_hub"),
        "USER": os.environ.get("DB_USER", "ikabot"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "ikabot"),
        "HOST": os.environ.get("DB_HOST", "mariadb"),
        "PORT": os.environ.get("DB_PORT", "3306"),
        "CONN_MAX_AGE": 600,  # Reuse DB connections for 10 min (avoids reconnect per request)
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }
}

# --- Cache (Redis) ---

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    }
}

# --- Sessions ---
# Use cached_db: reads from cache (fast), writes to both cache + DB (durable).

SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

# --- Auth ---

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

# --- REST Framework ---

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "core.auth.backends.AgentTokenAuthentication",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
        "rest_framework.filters.SearchFilter",
    ],
}

SPECTACULAR_SETTINGS = {
    "TITLE": "ikabot hub Agent API",
    "DESCRIPTION": (
        "Operational API used by ikabot agents to register nodes, "
        "update job status, submit snapshots, and proxy "
        "captcha/token operations."
    ),
    "VERSION": VERSION,
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/agent",
    "COMPONENT_SPLIT_REQUEST": True,
    "SECURITY": [{"AgentToken": []}],
    "SWAGGER_UI_SETTINGS": {
        "persistAuthorization": True,
    },
}

# --- i18n ---

LANGUAGE_CODE = "pt-br"
TIME_ZONE = os.environ.get("APP_TIMEZONE", "America/Cuiaba")
USE_I18N = True
USE_L10N = True
USE_TZ = True

LANGUAGES = [
    ("pt-br", "Português (Brasil)"),
    ("en", "English"),
]

LOCALE_PATHS = [
    BASE_DIR / "locale",
]

# --- Static files ---

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- Custom settings (ikabot hub) ---

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
APP_SECRET = os.environ.get("APP_SECRET", "troque-este-segredo")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "troque-agent-token")
AGENT_ALLOWED_IPS = os.environ.get("AGENT_ALLOWED_IPS", "")
IKABOTAPI_URL = os.environ.get("IKABOTAPI_URL", "http://ikabotapi:5005")
WEBSHARE_API_KEY = os.environ.get("WEBSHARE_API_KEY", "")

# --- Celery ---

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_DEFAULT_QUEUE = "ikabot.default"
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_TRACK_STARTED = False
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# --- Default primary key ---

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
