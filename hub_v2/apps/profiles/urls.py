from django.urls import path

from .views import (
    ProfileListView,
    ProfileDetailView,
    ProfileCreateView,
    ProfileEditView,
    ProfileDeleteView,
    ProfileRunActiveView,
)

app_name = "profiles"

urlpatterns = [
    path("", ProfileListView.as_view(), name="profile-list"),
    path("create/", ProfileCreateView.as_view(), name="profile-create"),
    path("<uuid:pk>/", ProfileDetailView.as_view(), name="profile-detail"),
    path("<uuid:pk>/edit/", ProfileEditView.as_view(), name="profile-edit"),
    path("<uuid:pk>/run-active/", ProfileRunActiveView.as_view(), name="profile-run-active"),
    path("<uuid:pk>/delete/", ProfileDeleteView.as_view(), name="profile-delete"),
]
