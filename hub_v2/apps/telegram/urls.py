from django.urls import path

from .views import (
    TelegramConfigView,
    TelegramSaveNotificationsView,
    TelegramAuditView,
    AccountTelegramOverviewView,
    GameAccountTelegramSettingsView,
    StartLinkingView,
    CheckLinkStatusView,
    UnlinkView,
    GlobalStartLinkingView,
    GlobalCheckLinkStatusView,
    GlobalUnlinkView,
    TemplateEditView,
    TemplateRowView,
    TemplateResetView,
)

app_name = "telegram"

urlpatterns = [
    # Global config
    path("", TelegramConfigView.as_view(), name="config"),
    path("save-notifications/", TelegramSaveNotificationsView.as_view(), name="save-notifications"),
    path("audit/", TelegramAuditView.as_view(), name="audit"),

    # Notification templates
    path("template/<int:pk>/", TemplateRowView.as_view(), name="template-row"),
    path("template/<int:pk>/edit/", TemplateEditView.as_view(), name="template-edit"),
    path("template/<int:pk>/reset/", TemplateResetView.as_view(), name="template-reset"),

    # Global linking
    path("start-link/", GlobalStartLinkingView.as_view(), name="global-start-link"),
    path("link-status/", GlobalCheckLinkStatusView.as_view(), name="global-link-status"),
    path("unlink/", GlobalUnlinkView.as_view(), name="global-unlink"),

    # Per-account overview (sidebar card)
    path(
        "account/<uuid:pk>/overview/",
        AccountTelegramOverviewView.as_view(),
        name="account-overview",
    ),

    # Per-GameAccount settings (HTMX expandable)
    path(
        "ga/<uuid:pk>/settings/",
        GameAccountTelegramSettingsView.as_view(),
        name="ga-settings",
    ),

    # Per-GA linking flow
    path("ga/<uuid:pk>/start-link/", StartLinkingView.as_view(), name="ga-start-link"),
    path("ga/<uuid:pk>/link-status/", CheckLinkStatusView.as_view(), name="ga-link-status"),
    path("ga/<uuid:pk>/unlink/", UnlinkView.as_view(), name="ga-unlink"),
]
