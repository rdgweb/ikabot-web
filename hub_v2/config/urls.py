"""
Root URL configuration for hub_v2.
"""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.defaults import page_not_found, server_error

from .views import ApiDocsView, AuthenticatedSchemaView, HtmxLogoutView


# Custom error handlers
handler404 = "config.views.custom_404"
handler500 = "config.views.custom_500"

urlpatterns = [
    # Django admin
    path("django-admin/", admin.site.urls),
    # Authentication
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", HtmxLogoutView.as_view(), name="logout"),
    # API documentation
    path("api/schema/", AuthenticatedSchemaView.as_view(), name="api-schema"),
    path("api/docs/", ApiDocsView.as_view(), name="api-docs"),
    # --- Operational ---
    path("", include("apps.dashboard.urls")),
    path("jobs/", include("apps.jobs.urls")),
    path("game/", include("apps.game.urls")),
    path("market/", include("apps.market.urls")),
    path("diplomacy/", include("apps.diplomacy.urls")),
    # --- Admin ---
    path("accounts/", include("apps.accounts.urls")),
    path("profiles/", include("apps.profiles.urls")),
    path("proxy/", include("apps.proxy.urls")),
    path("telegram/", include("apps.telegram.urls")),
    path("captcha/", include("apps.captcha.urls")),
    path("settings/", include("apps.settings_app.urls")),
    path("users/", include("apps.users.urls")),
    # --- Telegram Webhook API ---
    path("api/telegram/", include("apps.telegram.api_urls")),
    # --- Agent API ---
    path("api/agent/", include("apps.accounts.api_urls", namespace="agent-accounts")),
    path("api/agent/", include("apps.jobs.api_urls", namespace="agent-jobs")),
    path("api/agent/", include("apps.game.api_urls", namespace="agent-game")),
    path("api/agent/", include("apps.telegram.api.agent_urls", namespace="agent-telegram")),
    path("api/agent/", include("apps.market.api_urls", namespace="agent-market")),
    path("api/agent/", include("apps.diplomacy.api_urls", namespace="agent-diplomacy")),
]
