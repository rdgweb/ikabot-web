"""
User CRUD views — restricted to admin/staff users.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DeleteView

from core.mixins.views import StaffRequiredMixin
from .forms import UserCreateForm, UserEditForm


class UserListView(LoginRequiredMixin, StaffRequiredMixin, ListView):
    model = User
    template_name = "users/user_list.html"
    paginate_by = 25
    ordering = ["username"]
    context_object_name = "object_list"

    def get_queryset(self):
        return super().get_queryset().prefetch_related("groups")


class UserCreateView(LoginRequiredMixin, StaffRequiredMixin, CreateView):
    model = User
    form_class = UserCreateForm
    template_name = "users/user_create.html"
    success_url = reverse_lazy("users:list")


class UserEditView(LoginRequiredMixin, StaffRequiredMixin, UpdateView):
    model = User
    form_class = UserEditForm
    template_name = "users/user_edit.html"
    success_url = reverse_lazy("users:list")


class UserDeleteView(LoginRequiredMixin, StaffRequiredMixin, DeleteView):
    model = User
    template_name = "users/user_confirm_delete.html"
    success_url = reverse_lazy("users:list")
