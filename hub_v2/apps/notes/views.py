from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.shortcuts import redirect
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from .forms import ChangeLogEntryForm, NoteEventForm, NoteForm
from .models import ChangeLogEntry, Note, NoteEvent


def _actor_label(request) -> str:
    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        return str(user.get_username() or user)
    return "sistema"


def _record_note_event(note: Note, event_type: str, message: str, actor_label: str = "", **metadata):
    return NoteEvent.objects.create(
        note=note,
        event_type=event_type,
        message=message,
        actor_label=actor_label,
        metadata=metadata,
    )


class NoteListView(LoginRequiredMixin, ListView):
    model = Note
    template_name = "notes/list.html"
    context_object_name = "notes"
    paginate_by = 30

    def get_queryset(self):
        qs = Note.objects.select_related("created_by")
        status = (self.request.GET.get("status") or "active").strip()
        note_type = (self.request.GET.get("type") or "").strip()
        priority = (self.request.GET.get("priority") or "").strip()
        q = (self.request.GET.get("q") or "").strip()
        if status == "active":
            qs = qs.exclude(status__in=["done", "archived"])
        elif status:
            qs = qs.filter(status=status)
        if note_type:
            qs = qs.filter(note_type=note_type)
        if priority:
            qs = qs.filter(priority=priority)
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(body__icontains=q) | Q(tags__icontains=q))
        return qs.order_by("status", "-updated_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["status_filter"] = (self.request.GET.get("status") or "active").strip()
        ctx["type_filter"] = (self.request.GET.get("type") or "").strip()
        ctx["priority_filter"] = (self.request.GET.get("priority") or "").strip()
        ctx["q"] = (self.request.GET.get("q") or "").strip()
        ctx["status_choices"] = Note.STATUS_CHOICES
        ctx["type_choices"] = Note.TYPE_CHOICES
        ctx["priority_choices"] = Note.PRIORITY_CHOICES
        ctx["open_count"] = Note.objects.exclude(status__in=["done", "archived"]).count()
        ctx["authorized_count"] = Note.objects.filter(status="authorized").count()
        ctx["done_count"] = Note.objects.filter(status="done").count()
        ctx["doing_count"] = Note.objects.filter(status="doing").count()
        ctx["pending_approval_count"] = Note.objects.filter(status="pending_approval").count()
        ctx["urgent_count"] = Note.objects.filter(priority="urgent").exclude(status__in=["done", "archived"]).count()
        ctx["has_filters"] = bool(
            ctx["q"]
            or ctx["type_filter"]
            or ctx["priority_filter"]
            or ctx["status_filter"] not in ("", "active")
        )
        return ctx


class NoteDetailView(LoginRequiredMixin, DetailView):
    model = Note
    template_name = "notes/detail.html"
    context_object_name = "note"

    def get_queryset(self):
        return Note.objects.select_related("created_by").prefetch_related("changes", "events")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["event_form"] = NoteEventForm()
        return ctx


class NoteCreateView(LoginRequiredMixin, CreateView):
    model = Note
    form_class = NoteForm
    template_name = "notes/form.html"
    success_url = reverse_lazy("notes:list")

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        _record_note_event(self.object, "created", "Nota criada.", _actor_label(self.request))
        messages.success(self.request, f"Nota {self.object.code} criada.")
        return response


class NoteUpdateView(LoginRequiredMixin, UpdateView):
    model = Note
    form_class = NoteForm
    template_name = "notes/form.html"

    def get_success_url(self):
        return reverse_lazy("notes:detail", kwargs={"pk": self.object.pk})

    def form_valid(self, form):
        old_status = Note.objects.filter(pk=self.object.pk).values_list("status", flat=True).first()
        response = super().form_valid(form)
        if old_status != self.object.status:
            event_type = "authorized" if self.object.status == "authorized" else "status_change"
            _record_note_event(
                self.object,
                event_type,
                f"Status alterado de {old_status or '-'} para {self.object.status}.",
                _actor_label(self.request),
                old_status=old_status,
                new_status=self.object.status,
            )
        else:
            _record_note_event(self.object, "progress", "Nota atualizada.", _actor_label(self.request))
        messages.success(self.request, "Nota atualizada.")
        return response


class NoteClaimView(LoginRequiredMixin, View):
    def post(self, request, pk):
        note = get_object_or_404(Note, pk=pk)
        actor = request.POST.get("actor_label") or _actor_label(request)
        note.status = "doing"
        note.claimed_by_label = actor
        note.claimed_at = timezone.now()
        note.save(update_fields=["status", "claimed_by_label", "claimed_at", "updated_at"])
        _record_note_event(note, "claimed", f"Task assumida por {actor}.", actor)
        messages.success(request, f"{note.code} assumida por {actor}.")
        return redirect("notes:detail", pk=note.pk)


class NoteDoneView(LoginRequiredMixin, View):
    def post(self, request, pk):
        note = get_object_or_404(Note, pk=pk)
        if note.status == "done":
            messages.info(request, "Nota ja concluida.")
            return redirect("notes:detail", pk=note.pk)
        note.status = "pending_approval"
        note.completed_at = None
        note.save(update_fields=["status", "completed_at", "updated_at"])
        _record_note_event(
            note,
            "approval_requested",
            "Entrega enviada para aprovacao. Revise as informacoes, validacoes e pendencias registradas antes de concluir.",
            _actor_label(request),
        )
        messages.success(request, "Nota enviada para aprovacao.")
        return redirect("notes:detail", pk=note.pk)


class NoteApproveView(LoginRequiredMixin, View):
    def post(self, request, pk):
        note = get_object_or_404(Note, pk=pk, status="pending_approval")
        actor = _actor_label(request)
        _record_note_event(note, "approved", "Entrega aprovada.", actor)
        note.status = "done"
        note.completed_at = timezone.now()
        note.save(update_fields=["status", "completed_at", "updated_at"])
        _record_note_event(note, "completed", "Nota concluida apos aprovacao.", actor)
        messages.success(request, "Nota concluida.")
        return redirect("notes:detail", pk=note.pk)


class NoteEventCreateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        note = get_object_or_404(Note, pk=pk)
        form = NoteEventForm(request.POST)
        if form.is_valid():
            event = form.save(commit=False)
            event.note = note
            event.actor_label = _actor_label(request)
            event.save()
            messages.success(request, "Historico registrado.")
        else:
            messages.error(request, "Nao foi possivel registrar o historico.")
        return redirect("notes:detail", pk=note.pk)


class ChangeLogListView(LoginRequiredMixin, ListView):
    model = ChangeLogEntry
    template_name = "notes/changelog.html"
    context_object_name = "entries"
    paginate_by = 40

    def get_queryset(self):
        qs = ChangeLogEntry.objects.select_related("created_by", "note")
        component = (self.request.GET.get("component") or "").strip()
        visibility = (self.request.GET.get("visibility") or "").strip()
        if component:
            qs = qs.filter(component=component)
        if visibility:
            qs = qs.filter(visibility=visibility)
        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["component_filter"] = (self.request.GET.get("component") or "").strip()
        ctx["visibility_filter"] = (self.request.GET.get("visibility") or "").strip()
        ctx["component_choices"] = ChangeLogEntry.COMPONENT_CHOICES
        ctx["visibility_choices"] = ChangeLogEntry.VISIBILITY_CHOICES
        ctx["dev_count"] = ChangeLogEntry.objects.filter(visibility="dev").count()
        ctx["published_count"] = ChangeLogEntry.objects.filter(visibility="published").count()
        return ctx


class ChangeLogCreateView(LoginRequiredMixin, CreateView):
    model = ChangeLogEntry
    form_class = ChangeLogEntryForm
    template_name = "notes/changelog_form.html"
    success_url = reverse_lazy("notes:changelog")

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        if self.object.note_id:
            _record_note_event(
                self.object.note,
                "changelog",
                f"Changelog registrado: {self.object.title}",
                _actor_label(self.request),
                changelog_id=str(self.object.pk),
                visibility=self.object.visibility,
                version=self.object.version,
            )
        messages.success(self.request, "Atualizacao registrada.")
        return response
