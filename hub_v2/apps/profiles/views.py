"""
Profile CRUD views.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import DetailView, CreateView, UpdateView, DeleteView

from core.mixins.views import FilterSortListView
from .models import Profile
from .filters import ProfileFilter
from .forms import ProfileForm


class ProfileListView(FilterSortListView):
    model = Profile
    filterset_class = ProfileFilter
    template_name = "profiles/profile_list.html"
    partial_template_name = "profiles/partials/profile_table.html"
    paginate_by = 25
    ordering_fields = ["name", "action_code", "created_at"]
    default_ordering = "name"


class ProfileDetailView(LoginRequiredMixin, DetailView):
    model = Profile
    template_name = "profiles/profile_detail.html"
    context_object_name = "profile"


class ProfileCreateView(LoginRequiredMixin, CreateView):
    model = Profile
    form_class = ProfileForm
    template_name = "profiles/profile_create.html"
    success_url = reverse_lazy("profiles:profile-list")


class ProfileEditView(LoginRequiredMixin, UpdateView):
    model = Profile
    form_class = ProfileForm
    template_name = "profiles/profile_edit.html"

    def get_success_url(self):
        return reverse_lazy("profiles:profile-detail", kwargs={"pk": self.object.pk})


class ProfileDeleteView(LoginRequiredMixin, DeleteView):
    model = Profile
    success_url = reverse_lazy("profiles:profile-list")
    template_name = "profiles/profile_confirm_delete.html"
