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
EXPECTED_AGENT_VERSION = os.environ.get("EXPECTED_AGENT_VERSION", "").strip()

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

# N-107: the address the hub is opened at is enough. Behind a proxy (or opened by a domain or an IP
# that is not localhost) its host must be accepted and its origin trusted for the login form,
# which otherwise fails with a CSRF error nobody can read.
_PUBLIC_URL = os.environ.get("HUB_PUBLIC_URL", "").strip().rstrip("/")
HUB_PUBLIC_URL = _PUBLIC_URL
if _PUBLIC_URL.startswith(("http://", "https://")):
    from urllib.parse import urlsplit

    _public = urlsplit(_PUBLIC_URL)
    if _public.hostname and _public.hostname not in ALLOWED_HOSTS and "*" not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_public.hostname)
    _origin = f"{_public.scheme}://{_public.netloc}"
    if _origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(_origin)

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
    "apps.telemetry",
    "apps.manual_control",
    "apps.orgs",
    "apps.audit",
    "apps.website",
    "apps.billing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.website.middleware.SearchEnginesMiddleware",
    "core.middleware.MemoryWatchMiddleware",   # N-132: which request makes a process swell
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.orgs.middleware.OrganizationScopeMiddleware",
    "apps.orgs.middleware.PlatformPagesMiddleware",
    "apps.audit.middleware.AuditMiddleware",
    "apps.billing.middleware.ReadOnlyOrganizationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "core.middleware.HtmxBoostMiddleware",
    "apps.telemetry.middleware.TelemetryMiddleware",
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
                "apps.billing.context_processors.billing_notice",
                "apps.website.password.site_flags",
                "apps.telemetry.context.telemetry_prompt",
                "apps.settings_app.tour.tour_context",
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
# N-106: no built-in default — without the variable there is no single key at all (fail closed)
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")
if AGENT_TOKEN in ("troque-agent-token", "change-me", "changeme", "change-me-agent-token"):
    AGENT_TOKEN = ""
# the single key of the installation, while the agents move to their own node keys
AGENT_SHARED_TOKEN_ENABLED = os.environ.get("AGENT_SHARED_TOKEN_ENABLED", "true").strip().lower() in ("1", "true", "yes")
AGENT_ALLOWED_IPS = os.environ.get("AGENT_ALLOWED_IPS", "")
IKABOTAPI_URL = os.environ.get("IKABOTAPI_URL", "http://ikabotapi:5005")

# --- Telemetry (opt-in; see apps/telemetry and telemetry_v1) ---
TELEMETRY_URL = os.environ.get("TELEMETRY_URL") or "https://telemetry.rdgh.com.br"
TELEMETRY_DISABLED = os.environ.get("TELEMETRY_DISABLED", "false").lower() in ("true", "1", "yes")
WEBSHARE_API_KEY = os.environ.get("WEBSHARE_API_KEY", "")

# --- Public site (N-119) ---
# SITE_URL: the address the site is published at (canonical, sitemap, previews); empty = HUB_PUBLIC_URL.
# SITE_INDEXABLE: search engines may index the site pages (the panel never is).
SITE_URL = (os.environ.get("SITE_URL", "").strip() or HUB_PUBLIC_URL).rstrip("/")
SITE_INDEXABLE = os.environ.get("SITE_INDEXABLE", "false").strip().lower() in ("1", "true", "yes")
# who answers for the service in the terms and the privacy policy; each line shows only when filled
SITE_LEGAL = {
    "name": os.environ.get("SITE_LEGAL_NAME", "").strip(),
    "document": os.environ.get("SITE_LEGAL_DOCUMENT", "").strip(),
    "city": os.environ.get("SITE_LEGAL_CITY", "").strip(),
    "email": os.environ.get("SITE_CONTACT_EMAIL", "").strip(),
    "privacy_email": os.environ.get("SITE_PRIVACY_EMAIL", "").strip() or os.environ.get("SITE_CONTACT_EMAIL", "").strip(),
    "telegram": os.environ.get("SITE_CONTACT_TELEGRAM", "").strip().lstrip("@"),
}
# visit statistics (Umami in the same server, never published): the hub hands out only the script and the count
UMAMI_URL = os.environ.get("UMAMI_URL", "").strip().rstrip("/")
UMAMI_WEBSITE_ID = os.environ.get("UMAMI_WEBSITE_ID", "").strip()

# --- E-mail (N-125): the owner's own SMTP (app password), only in the environment ---
EMAIL_HOST = os.environ.get("EMAIL_HOST", "").strip()
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587").strip() or 587)
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "").strip()
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "").strip()
EMAIL_USE_SSL = EMAIL_PORT == 465
EMAIL_USE_TLS = not EMAIL_USE_SSL
EMAIL_TIMEOUT = 15
EMAIL_READY = bool(EMAIL_HOST and EMAIL_HOST_USER and EMAIL_HOST_PASSWORD)
DEFAULT_FROM_EMAIL = os.environ.get("EMAIL_FROM", "").strip() or (f"IkaHub <{EMAIL_HOST_USER}>" if EMAIL_HOST_USER else "IkaHub <nao-responda@localhost>")
# without SMTP nothing is sent (never to the log: the messages carry sign-in links)
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend" if EMAIL_READY else "django.core.mail.backends.dummy.EmailBackend"
PASSWORD_RESET_TIMEOUT = 2 * 3600
# open sign-up on the site (with SMTP); off = by invitation only
SIGNUP_OPEN = os.environ.get("SIGNUP_OPEN", "false").strip().lower() in ("1", "true", "yes")

# --- Platform agents (N-131): the platform's machine (a Docker host with its supervisor) ---
PLATFORM_AGENT_HOST_ID = os.environ.get("PLATFORM_AGENT_HOST_ID", "").strip()
PLATFORM_AGENT_LIMIT = int(os.environ.get("PLATFORM_AGENT_LIMIT", "10").strip() or 10)
PLATFORM_AGENT_MEMORY_MB = int(os.environ.get("PLATFORM_AGENT_MEMORY_MB", "256").strip() or 256)

# --- Billing (N-124): Asaas. The key only in the environment; sandbox until said otherwise ---
ASAAS_API_KEY = os.environ.get("ASAAS_API_KEY", "").strip()
ASAAS_SANDBOX = os.environ.get("ASAAS_SANDBOX", "true").strip().lower() in ("1", "true", "yes")
ASAAS_WEBHOOK_TOKEN = os.environ.get("ASAAS_WEBHOOK_TOKEN", "").strip()

# --- Celery ---

# N-72: manual control. Each session of the game is served at <token>.<this domain>
# by the gateway process; empty = the feature is off (no button, nothing changes).
MANUAL_GATEWAY_DOMAIN = os.environ.get("MANUAL_GATEWAY_DOMAIN", "").strip().strip(".").lower()
MANUAL_GATEWAY_SCHEME = os.environ.get("MANUAL_GATEWAY_SCHEME", "https").strip() or "https"
MANUAL_GATEWAY_PORT = os.environ.get("MANUAL_GATEWAY_PORT", "").strip()

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_DEFAULT_QUEUE = "ikabot.default"
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_TRACK_STARTED = False
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# --- Default primary key ---

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
