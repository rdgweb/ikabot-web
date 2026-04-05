"""
Views para configuracoes do sistema.
"""

import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import TemplateView, UpdateView

from core.encryption import decrypt, encrypt
from .forms import AppSettingForm, WebshareSettingsForm, IkabotApiSettingsForm, AgentSecurityForm, SnapshotPolicyForm
from .models import AppSetting


def _get_setting(key, default=""):
    try:
        return AppSetting.objects.get(key=key).value
    except AppSetting.DoesNotExist:
        return default


def _set_setting(key, value):
    AppSetting.objects.update_or_create(key=key, defaults={"value": value})


def _get_encrypted_setting(key, default=""):
    raw = _get_setting(key, "")
    if not raw:
        return default
    try:
        return decrypt(raw)
    except Exception:
        return default


def _set_encrypted_setting(key, value):
    _set_setting(key, encrypt(value))


class SettingsPageView(LoginRequiredMixin, TemplateView):
    template_name = "settings_app/page.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ws_key = _get_encrypted_setting("webshare_api_key", "")
        ctx["webshare_form"] = WebshareSettingsForm()
        ctx["webshare_configured"] = bool(ws_key)
        ctx["webshare_masked"] = ("*" * 8 + ws_key[-6:]) if len(ws_key) > 6 else ("*" * len(ws_key) if ws_key else "")

        ika_url = _get_setting("ikabotapi_url", settings.IKABOTAPI_URL)
        ctx["ikabotapi_form"] = IkabotApiSettingsForm(initial={"url": ika_url})
        ctx["ikabotapi_url"] = ika_url

        agent_token = settings.AGENT_TOKEN
        allowed_ips = _get_setting("agent_allowed_ips", settings.AGENT_ALLOWED_IPS)
        ctx["agent_form"] = AgentSecurityForm(initial={"allowed_ips": allowed_ips})
        ctx["agent_token"] = agent_token
        ctx["agent_token_masked"] = ("*" * 8 + agent_token[-6:]) if len(agent_token) > 6 else "*" * len(agent_token)

        ctx["snapshot_policy_form"] = SnapshotPolicyForm(initial={
            "snapshot_stale_seconds": _get_setting("snapshot_stale_seconds", "7200"),
            "building_options_stale_seconds": _get_setting("building_options_stale_seconds", "21600"),
            "running_job_recovery_grace_seconds": _get_setting("running_job_recovery_grace_seconds", "300"),
            "running_job_lease_seconds": _get_setting("running_job_lease_seconds", "180"),
        })

        ctx["generic_settings"] = AppSetting.objects.exclude(
            key__in=[
                "webshare_api_key",
                "ikabotapi_url",
                "agent_allowed_ips",
                "snapshot_stale_seconds",
                "building_options_stale_seconds",
                "running_job_recovery_grace_seconds",
                "running_job_lease_seconds",
            ]
        ).order_by("key")

        return ctx


class WebshareSaveView(LoginRequiredMixin, View):
    def post(self, request):
        form = WebshareSettingsForm(request.POST)
        if form.is_valid():
            api_key = form.cleaned_data["api_key"]
            if api_key:
                _set_encrypted_setting("webshare_api_key", api_key)
                messages.success(request, "Chave API Webshare salva.")
            else:
                messages.info(request, "Nenhuma alteracao (campo vazio).")
        return redirect("settings_app:list")


class WebshareTestView(LoginRequiredMixin, View):
    def post(self, request):
        from .services import test_webshare

        api_key = _get_encrypted_setting("webshare_api_key", "")
        if not api_key:
            api_key = getattr(settings, "WEBSHARE_API_KEY", "")

        result = test_webshare(api_key)
        if result["success"]:
            html = (
                '<div class="flex items-center gap-2 px-3 py-2 rounded text-xs" style="background: #e8f2e0;">'
                '<i class="bi bi-check-circle-fill" style="color: var(--ik-good);"></i>'
                f'<span>Conexao OK - <strong>{result["proxy_count"]}</strong> proxies encontrados</span>'
                '</div>'
            )
        else:
            html = (
                '<div class="flex items-center gap-2 px-3 py-2 rounded text-xs" style="background: #fde8e5;">'
                '<i class="bi bi-x-circle-fill" style="color: var(--ik-bad);"></i>'
                f'<span>{result["error"]}</span>'
                '</div>'
            )
        return HttpResponse(html)


class IkabotApiSaveView(LoginRequiredMixin, View):
    def post(self, request):
        form = IkabotApiSettingsForm(request.POST)
        if form.is_valid():
            url = form.cleaned_data["url"]
            if url:
                _set_setting("ikabotapi_url", url)
                messages.success(request, "URL do ikabotapi salva.")
            else:
                messages.info(request, "Nenhuma alteracao (campo vazio).")
        return redirect("settings_app:list")


class IkabotApiTestView(LoginRequiredMixin, View):
    def post(self, request):
        from .services import test_ikabotapi

        url = _get_setting("ikabotapi_url", getattr(settings, "IKABOTAPI_URL", ""))
        result = test_ikabotapi(url)
        if result["success"]:
            html = (
                '<div class="flex items-center gap-2 px-3 py-2 rounded text-xs" style="background: #e8f2e0;">'
                '<i class="bi bi-check-circle-fill" style="color: var(--ik-good);"></i>'
                f'<span>Conexao OK - {result["detail"]}</span>'
                '</div>'
            )
        else:
            html = (
                '<div class="flex items-center gap-2 px-3 py-2 rounded text-xs" style="background: #fde8e5;">'
                '<i class="bi bi-x-circle-fill" style="color: var(--ik-bad);"></i>'
                f'<span>{result["error"]}</span>'
                '</div>'
            )
        return HttpResponse(html)


class AgentSecuritySaveView(LoginRequiredMixin, View):
    def post(self, request):
        form = AgentSecurityForm(request.POST)
        if form.is_valid():
            _set_setting("agent_allowed_ips", form.cleaned_data["allowed_ips"])
            messages.success(request, "Configuracao de seguranca do agent salva.")
        return redirect("settings_app:list")


class SnapshotPolicySaveView(LoginRequiredMixin, View):
    def post(self, request):
        form = SnapshotPolicyForm(request.POST)
        if form.is_valid():
            _set_setting("snapshot_stale_seconds", str(form.cleaned_data["snapshot_stale_seconds"]))
            _set_setting("building_options_stale_seconds", str(form.cleaned_data["building_options_stale_seconds"]))
            _set_setting("running_job_recovery_grace_seconds", str(form.cleaned_data["running_job_recovery_grace_seconds"]))
            _set_setting("running_job_lease_seconds", str(form.cleaned_data["running_job_lease_seconds"]))
            messages.success(request, "Politica de snapshot salva.")
        return redirect("settings_app:list")


class SettingEditView(LoginRequiredMixin, UpdateView):
    model = AppSetting
    form_class = AppSettingForm
    template_name = "settings_app/setting_edit.html"
    slug_field = "key"
    slug_url_kwarg = "key"
    success_url = reverse_lazy("settings_app:list")

    def form_valid(self, form):
        messages.success(self.request, f"Setting '{form.instance.key}' salva.")
        return super().form_valid(form)

    def get_object(self, queryset=None):
        return AppSetting.objects.get(pk=self.kwargs["key"])
