"""
User CRUD views — restricted to admin/staff users.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DeleteView

from core.mixins.views import StaffRequiredMixin
from .forms import UserCreateForm, UserEditForm
from core.access import platform_owner
from django.http import Http404
from django.core.exceptions import ValidationError
from django.db import transaction


class PrincipalOnly:
    def dispatch(self, request, *args, **kwargs):
        if not platform_owner(request.user):
            raise Http404()
        return super().dispatch(request, *args, **kwargs)


class UserListView(PrincipalOnly, LoginRequiredMixin, StaffRequiredMixin, ListView):
    model = User
    template_name = "users/user_list.html"
    paginate_by = 25
    ordering = ["username"]
    context_object_name = "object_list"

    def get_queryset(self):
        return super().get_queryset().prefetch_related("groups")


class UserCreateView(PrincipalOnly, LoginRequiredMixin, StaffRequiredMixin, CreateView):
    model = User
    form_class = UserCreateForm
    template_name = "users/user_create.html"
    success_url = reverse_lazy("users:list")


class UserEditView(PrincipalOnly, LoginRequiredMixin, StaffRequiredMixin, UpdateView):
    model = User
    form_class = UserEditForm
    template_name = "users/user_edit.html"
    success_url = reverse_lazy("users:list")

    @transaction.atomic
    def form_valid(self, form):
        if not form.cleaned_data.get("is_active"):
            from apps.access_control.services import lock, protect_owner_identity
            lock()
            try:
                protect_owner_identity(User.objects.get(pk=self.object.pk))
            except ValidationError as exc:
                form.add_error("is_active", exc)
                return self.form_invalid(form)
        return super().form_valid(form)


class UserDeleteView(PrincipalOnly, LoginRequiredMixin, StaffRequiredMixin, DeleteView):
    model = User
    template_name = "users/user_confirm_delete.html"
    success_url = reverse_lazy("users:list")

    def get_queryset(self):
        return super().get_queryset().filter(is_superuser=False)

    @transaction.atomic
    def form_valid(self, form):
        from apps.access_control.services import lock, protect_owner_identity
        from django.http import HttpResponseForbidden
        lock()
        try:
            protect_owner_identity(self.object)
        except ValidationError as exc:
            return HttpResponseForbidden(" ".join(exc.messages))
        return super().form_valid(form)
