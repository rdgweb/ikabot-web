from django.urls import path

from .views import (
    # Legacy Profile views
    ProfileListView,
    ProfileDetailView,
    ProfileCreateView,
    ProfileEditView,
    ProfileDeleteView,
    ProfileRunActiveView,
    # Preset (F006) views
    PresetListView,
    PresetCreateView,
    PresetDetailView,
    PresetSaveAccountsView,
    PresetSaveActionsView,
    PresetRemoveActionView,
    PresetConfigureActionView,
    PresetExecuteView,
    PresetUpdateView,
    PresetDeleteView,
)

app_name = "profiles"

urlpatterns = [
    # ── Legacy Profile ────────────────────────────────────────────────────────
    path("", ProfileListView.as_view(), name="profile-list"),
    path("create/", ProfileCreateView.as_view(), name="profile-create"),
    path("<uuid:pk>/", ProfileDetailView.as_view(), name="profile-detail"),
    path("<uuid:pk>/edit/", ProfileEditView.as_view(), name="profile-edit"),
    path("<uuid:pk>/run-active/", ProfileRunActiveView.as_view(), name="profile-run-active"),
    path("<uuid:pk>/delete/", ProfileDeleteView.as_view(), name="profile-delete"),

    # ── Preset (F006) ─────────────────────────────────────────────────────────
    path("presets/", PresetListView.as_view(), name="preset-list"),
    path("presets/new/", PresetCreateView.as_view(), name="preset-create"),
    path("presets/<uuid:pk>/", PresetDetailView.as_view(), name="preset-detail"),
    path("presets/<uuid:pk>/accounts/", PresetSaveAccountsView.as_view(), name="preset-save-accounts"),
    path("presets/<uuid:pk>/actions/", PresetSaveActionsView.as_view(), name="preset-save-actions"),
    path("presets/<uuid:pk>/actions/<int:action_order>/remove/", PresetRemoveActionView.as_view(), name="preset-remove-action"),
    path("presets/<uuid:pk>/configure/<int:action_order>/", PresetConfigureActionView.as_view(), name="preset-configure-action"),
    path("presets/<uuid:pk>/execute/", PresetExecuteView.as_view(), name="preset-execute"),
    path("presets/<uuid:pk>/update/", PresetUpdateView.as_view(), name="preset-update"),
    path("presets/<uuid:pk>/delete/", PresetDeleteView.as_view(), name="preset-delete"),
]
