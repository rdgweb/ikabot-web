"""
Profile CRUD views + Preset (F006) views.
"""

import json
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import DetailView, CreateView, UpdateView, DeleteView

from apps.accounts.models import Account, GameAccount
from apps.jobs.models import Job
from apps.jobs.forms import JobCreateForm
from apps.jobs.views.create import (
    _construction_city_data,
    _get_cities,
    _job_form_context,
)
from core.contracts import ACTION_CATALOG, get_actions_for_ui
from core.mixins.views import FilterSortListView

from .filters import ProfileFilter
from .forms import ProfileForm
from .models import Preset, PresetAction, PresetGameAccount, Profile
from .services import execute_preset

logger = logging.getLogger(__name__)


# ── Legacy Profile views ───────────────────────────────────────────────────────


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


# ── Preset (F006) views ────────────────────────────────────────────────────────


def _get_all_active_gas():
    """Return all active, non-blocked GameAccounts grouped by Account."""
    accounts = Account.objects.filter(active=True).prefetch_related(
        "game_accounts"
    ).order_by("label")
    groups = []
    all_gas = []
    for account in accounts:
        gas = list(account.game_accounts.filter(active=True, blocked=False).order_by("server_id"))
        if gas:
            groups.append({"account": account, "game_accounts": gas})
            all_gas.extend(gas)
    return groups, all_gas


class PresetListView(LoginRequiredMixin, View):
    def get(self, request):
        presets = Preset.objects.prefetch_related("preset_accounts", "actions").order_by("name")
        return self._render(request, presets)

    def _render(self, request, presets):
        from django.shortcuts import render
        return render(request, "profiles/preset_list.html", {"presets": presets})


class PresetCreateView(LoginRequiredMixin, View):
    def get(self, request):
        from django.shortcuts import render
        return render(request, "profiles/preset_create.html", {})

    def post(self, request):
        name = request.POST.get("name", "").strip()
        description = request.POST.get("description", "").strip()

        if not name:
            from django.shortcuts import render
            return render(request, "profiles/preset_create.html", {
                "error": "O nome do preset e obrigatorio.",
                "name": name,
                "description": description,
            })

        if Preset.objects.filter(name=name).exists():
            from django.shortcuts import render
            return render(request, "profiles/preset_create.html", {
                "error": f"Ja existe um preset com o nome '{name}'.",
                "name": name,
                "description": description,
            })

        preset = Preset.objects.create(
            name=name,
            description=description,
            created_by=request.user,
        )
        messages.success(request, f"Preset '{preset.name}' criado com sucesso.")
        return redirect("profiles:preset-detail", pk=preset.pk)


class PresetDetailView(LoginRequiredMixin, View):
    def get(self, request, pk):
        from django.shortcuts import render
        preset = get_object_or_404(Preset, pk=pk)
        selected_ga_pks = set(
            preset.preset_accounts.values_list("game_account_id", flat=True)
        )
        _, all_gas = _get_all_active_gas()
        action_groups = get_actions_for_ui()
        selected_actions = list(preset.actions.all())

        return render(request, "profiles/preset_detail.html", {
            "preset": preset,
            "all_gas": all_gas,
            "selected_ga_pks": selected_ga_pks,
            "action_groups": action_groups,
            "selected_actions": selected_actions,
        })


class PresetSaveAccountsView(LoginRequiredMixin, View):
    """POST — HTMX: save selected game accounts for the preset."""

    def post(self, request, pk):
        preset = get_object_or_404(Preset, pk=pk)
        selected_pks = request.POST.getlist("game_accounts")

        # Replace all preset_accounts
        PresetGameAccount.objects.filter(preset=preset).delete()
        for ga_pk in selected_pks:
            try:
                ga = GameAccount.objects.get(pk=ga_pk)
                PresetGameAccount.objects.get_or_create(preset=preset, game_account=ga)
            except GameAccount.DoesNotExist:
                pass

        selected_ga_pks = set(
            preset.preset_accounts.values_list("game_account_id", flat=True)
        )
        _, all_gas = _get_all_active_gas()

        from django.template.loader import render_to_string
        html = render_to_string(
            "profiles/partials/preset_accounts_section.html",
            {
                "preset": preset,
                "all_gas": all_gas,
                "selected_ga_pks": selected_ga_pks,
            },
            request=request,
        )
        resp = HttpResponse(html)
        resp["HX-Trigger"] = json.dumps({"toast": {"type": "success", "message": "Contas salvas."}})
        return resp


class PresetSaveActionsView(LoginRequiredMixin, View):
    """POST — HTMX: add an action to the preset."""

    def post(self, request, pk):
        preset = get_object_or_404(Preset, pk=pk)
        action_code_str = request.POST.get("action_code", "").strip()

        if action_code_str:
            try:
                action_code = int(action_code_str)
                meta = ACTION_CATALOG.get(action_code, {})
                action_name = meta.get("name", f"Acao #{action_code}")
                # Determine next order
                max_order = preset.actions.order_by("-order").values_list("order", flat=True).first()
                next_order = (max_order or 0) + 1
                PresetAction.objects.create(
                    preset=preset,
                    action_code=action_code,
                    action_name=action_name,
                    order=next_order,
                    inputs_json="{}",
                    per_account_json="{}",
                )
            except (ValueError, TypeError):
                pass

        selected_actions = list(preset.actions.all())
        action_groups = get_actions_for_ui()

        from django.template.loader import render_to_string
        html = render_to_string(
            "profiles/partials/preset_actions_section.html",
            {
                "preset": preset,
                "selected_actions": selected_actions,
                "action_groups": action_groups,
            },
            request=request,
        )
        resp = HttpResponse(html)
        resp["HX-Trigger"] = json.dumps({"toast": {"type": "success", "message": "Acao adicionada."}})
        return resp


class PresetRemoveActionView(LoginRequiredMixin, View):
    """POST — HTMX: remove an action from the preset."""

    def post(self, request, pk, action_order):
        preset = get_object_or_404(Preset, pk=pk)
        PresetAction.objects.filter(preset=preset, order=action_order).delete()

        # Re-number remaining actions
        for i, pa in enumerate(preset.actions.order_by("order"), start=1):
            if pa.order != i:
                pa.order = i
                pa.save(update_fields=["order"])

        selected_actions = list(preset.actions.all())
        action_groups = get_actions_for_ui()

        from django.template.loader import render_to_string
        html = render_to_string(
            "profiles/partials/preset_actions_section.html",
            {
                "preset": preset,
                "selected_actions": selected_actions,
                "action_groups": action_groups,
            },
            request=request,
        )
        return HttpResponse(html)


class PresetConfigureActionView(LoginRequiredMixin, View):
    """GET/POST — configure one action's inputs (full page).

    City fields are stripped from the main form and handled in a dedicated
    city-config section that supports __all__, __auto__, or per-account city IDs.
    """

    # All city field names recognised across all actions
    CITY_FIELD_NAMES = [
        "city", "city_id", "cities", "from_city", "to_city",
        "from_city_id", "to_city_id", "buyer_city_id",
        "own_city_ids", "target_city", "source_city",
    ]

    def _city_meta(self, action_code, action_meta):
        """Return (city_field_name, city_type) for actions with city input.
        city_type: 'multi' | 'single' | 'route' | None
        """
        from core.actions.constants import FIELD_CITY_SELECT
        inputs = action_meta.get("inputs") or []
        city_keys = {inp["key"] for inp in inputs if inp.get("type") == FIELD_CITY_SELECT}
        if not city_keys:
            return None, None
        # Route actions have both from and to city
        if any(k in city_keys for k in ("from_city", "from_city_id")):
            return "route", "route"
        # Multi-city
        for k in ("cities", "own_city_ids"):
            if k in city_keys:
                return k, "multi"
        # Single city
        for k in self.CITY_FIELD_NAMES:
            if k in city_keys:
                return k, "single"
        return None, None

    def _account_cities(self, preset):
        """Return list of {ga, cities} for all accounts in the preset."""
        result = []
        for pga in preset.preset_accounts.select_related("game_account__account").order_by("id"):
            ga = pga.game_account
            try:
                cities = _get_cities(ga)
            except Exception:
                cities = []
            result.append({"ga": ga, "cities": cities})
        return result

    def _build_ctx(self, preset, pa, action_meta, form, ga, construction_cities, action_order):
        """Build template context shared between GET and POST."""
        city_field_name, city_type = self._city_meta(pa.action_code, action_meta)
        account_cities = self._account_cities(preset) if city_type else []

        try:
            per_account = json.loads(pa.per_account_json or "{}")
        except Exception:
            per_account = {}

        city_mode = per_account.get("__mode__", "all" if city_type == "multi" else "auto")

        base = _job_form_context(form, action_meta, pa.action_code, ga, construction_cities) if ga else {
            "form": form, "action_meta": action_meta, "action_code": pa.action_code,
            "game_account": None, "cities": [], "custom_field_names": [],
            "construction_buildings": {}, "shrine_ui": {}, "daily_login_ui": {},
            "research_ui": {}, "miracle_ui": {}, "experiment_ui": {},
            "scientists_ui": {}, "upgrade_units_ui": {}, "market_ui": {}, "training_ui": {},
        }
        base.update({
            "preset": preset,
            "preset_action": pa,
            "action_order": action_order,
            "next_order": action_order + 1 if preset.actions.filter(order=action_order + 1).exists() else None,
            # City config
            "city_field_name": city_field_name,
            "city_type": city_type,
            "city_mode": city_mode,
            "account_cities": account_cities,
            "per_account_city": per_account,
            # Tell _generic_fields.html to skip city fields
            "no_footer": True,
            "preset_city_fields": self.CITY_FIELD_NAMES,
        })
        return base

    def get(self, request, pk, action_order):
        from django.shortcuts import render
        preset = get_object_or_404(Preset, pk=pk)
        pa = get_object_or_404(PresetAction, preset=preset, order=action_order)
        action_meta = ACTION_CATALOG.get(pa.action_code, {})

        try:
            existing_inputs = json.loads(pa.inputs_json or "{}")
        except Exception:
            existing_inputs = {}

        first_pga = preset.preset_accounts.select_related("game_account__account").first()
        ga = first_pga.game_account if first_pga else None
        cities, construction_cities = [], []
        if ga:
            cities = _get_cities(ga)
            construction_cities = _construction_city_data(cities)

        initial = {"action_code": pa.action_code}
        if ga:
            initial["game_account"] = str(ga.pk)
        initial.update(existing_inputs)

        form = JobCreateForm(action_code=pa.action_code, game_account=ga, cities=construction_cities, initial=initial)
        ctx = self._build_ctx(preset, pa, action_meta, form, ga, construction_cities, action_order)
        return render(request, "profiles/preset_configure_action.html", ctx)

    def post(self, request, pk, action_order):
        from django.shortcuts import render
        preset = get_object_or_404(Preset, pk=pk)
        pa = get_object_or_404(PresetAction, preset=preset, order=action_order)
        action_meta = ACTION_CATALOG.get(pa.action_code, {})

        first_pga = preset.preset_accounts.select_related("game_account__account").first()
        ga = first_pga.game_account if first_pga else None
        cities, construction_cities = [], []
        if ga:
            cities = _get_cities(ga)
            construction_cities = _construction_city_data(cities)

        form = JobCreateForm(request.POST, action_code=pa.action_code, game_account=ga, cities=construction_cities)

        # --- Save city config from POST ---
        city_field_name, city_type = self._city_meta(pa.action_code, action_meta)
        city_mode = request.POST.get("city_mode", "all")
        per_account: dict = {"__mode__": city_mode}

        if city_type and city_mode == "per_account":
            for pga in preset.preset_accounts.select_related("game_account").all():
                ga_key = str(pga.game_account.pk)
                if city_type == "multi":
                    selected = request.POST.getlist(f"cities__{ga_key}")
                    per_account[ga_key] = {city_field_name: selected or "__all__"}
                elif city_type == "single":
                    selected = request.POST.get(f"city__{ga_key}", "")
                    per_account[ga_key] = {city_field_name: selected}
                elif city_type == "route":
                    per_account[ga_key] = {
                        "from_city": request.POST.get(f"from_city__{ga_key}", ""),
                        "to_city": request.POST.get(f"to_city__{ga_key}", ""),
                    }

        pa.per_account_json = json.dumps(per_account)

        if form.is_valid():
            inputs = form.get_inputs_json()
            # Remove city fields from global inputs — they live in per_account_json
            for f in self.CITY_FIELD_NAMES:
                inputs.pop(f, None)
            pa.inputs_json = json.dumps(inputs)
            pa.save(update_fields=["inputs_json", "per_account_json", "updated_at"])
            messages.success(request, f"Configuracao da acao '{pa.action_name}' salva.")
            next_pa = preset.actions.filter(order__gt=action_order).first()
            if next_pa:
                return redirect("profiles:preset-configure-action", pk=preset.pk, action_order=next_pa.order)
            return redirect("profiles:preset-detail", pk=preset.pk)

        pa.save(update_fields=["per_account_json", "updated_at"])
        ctx = self._build_ctx(preset, pa, action_meta, form, ga, construction_cities, action_order)
        return render(request, "profiles/preset_configure_action.html", ctx)


class PresetExecuteView(LoginRequiredMixin, View):
    """POST — execute preset (create all jobs)."""

    def post(self, request, pk):
        preset = get_object_or_404(Preset, pk=pk)
        jobs_created, errors = execute_preset(preset, created_by=request.user)

        if jobs_created:
            messages.success(request, f"{jobs_created} job(s) criado(s) pelo preset '{preset.name}'.")
        if errors:
            for err in errors[:5]:
                messages.warning(request, err)
        if not jobs_created and not errors:
            messages.warning(request, "Nenhum job criado. Verifique se o preset tem contas e acoes configuradas.")

        return redirect("profiles:preset-detail", pk=preset.pk)


class PresetDeleteView(LoginRequiredMixin, View):
    """POST — delete preset."""

    def post(self, request, pk):
        preset = get_object_or_404(Preset, pk=pk)
        name = preset.name
        preset.delete()
        messages.success(request, f"Preset '{name}' excluido.")
        return redirect("profiles:preset-list")
