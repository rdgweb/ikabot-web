"""
Profile CRUD views.
"""

import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import DetailView, CreateView, UpdateView, DeleteView

from apps.accounts.models import GameAccount
from apps.jobs.models import Job
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


class ProfileRunActiveView(LoginRequiredMixin, View):
    """Create one job from this profile for every active game account."""

    def post(self, request, pk):
        profile = get_object_or_404(Profile, pk=pk)
        if not profile.enabled:
            messages.error(request, "Perfil inativo nao pode ser executado.")
            return redirect("profiles:profile-detail", pk=profile.pk)

        try:
            inputs = json.loads(profile.inputs_json or "{}")
        except Exception:
            messages.error(request, "Inputs JSON do perfil esta invalido.")
            return redirect("profiles:profile-detail", pk=profile.pk)

        if isinstance(inputs, list):
            normalized = {}
            for item in inputs:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key") or item.get("name") or "").strip()
                if key:
                    normalized[key] = item.get("value")
            inputs = normalized
        elif not isinstance(inputs, dict):
            messages.error(request, "Inputs JSON do perfil deve ser um objeto ou lista key/value.")
            return redirect("profiles:profile-detail", pk=profile.pk)

        game_accounts = (
            GameAccount.objects.select_related("account__node")
            .filter(active=True, blocked=False, account__active=True, account__node__active=True)
            .order_by("account__label", "server_id")
        )

        jobs = [
            Job(
                account=ga.account,
                game_account=ga,
                node=ga.account.node,
                profile=profile,
                action_code=profile.action_code,
                inputs_json=json.dumps(inputs),
                timeout_sec=profile.timeout_sec,
                status="queued",
            )
            for ga in game_accounts
        ]
        if jobs:
            Job.objects.bulk_create(jobs)

        messages.success(request, f"{len(jobs)} job(s) criado(s) a partir do perfil.")
        return redirect("profiles:profile-detail", pk=profile.pk)
