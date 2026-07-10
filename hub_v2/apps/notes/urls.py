from django.urls import path

from .views import (
    ChangeLogCreateView,
    ChangeLogListView,
    NoteCreateView,
    NoteDetailView,
    NoteDoneView,
    NoteApproveView,
    NoteClaimView,
    NoteEventCreateView,
    NoteListView,
    NoteUpdateView,
)

app_name = "notes"

urlpatterns = [
    path("", NoteListView.as_view(), name="list"),
    path("nova/", NoteCreateView.as_view(), name="create"),
    path("<uuid:pk>/", NoteDetailView.as_view(), name="detail"),
    path("<uuid:pk>/editar/", NoteUpdateView.as_view(), name="edit"),
    path("<uuid:pk>/assumir/", NoteClaimView.as_view(), name="claim"),
    path("<uuid:pk>/concluir/", NoteDoneView.as_view(), name="done"),
    path("<uuid:pk>/aprovar/", NoteApproveView.as_view(), name="approve"),
    path("<uuid:pk>/historico/", NoteEventCreateView.as_view(), name="event-create"),
    path("atualizacoes/", ChangeLogListView.as_view(), name="changelog"),
    path("atualizacoes/nova/", ChangeLogCreateView.as_view(), name="changelog-create"),
]
