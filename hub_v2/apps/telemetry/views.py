import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils.html import escape
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from core.mixins.views import StaffRequiredMixin

from . import services


def _back(request):
    target = request.POST.get("next") or ""
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)
    return redirect("settings_app:list")


class ConsentView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Explicit opt-in / decline. Nothing is sent before an admin posts here."""

    def post(self, request):
        if services.kill_switch_on():
            messages.info(request, "A telemetria está desligada por configuração do ambiente (TELEMETRY_DISABLED).")
        elif request.POST.get("choice") == "accept":
            services.enable()
            ok, message = services.send_now()
            messages.success(request, "Telemetria ativada. Obrigado por ajudar o projeto!" + ("" if ok else f" (primeiro envio pendente: {message})"))
        else:
            services.decline()
            messages.info(request, "Tudo bem, nada será enviado. Você pode ativar depois em Configurações → Telemetria.")
        return _back(request)


class DisableView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request):
        _, message = services.disable_and_erase()
        messages.success(request, message)
        return _back(request)


class SendNowView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request):
        ok, message = services.send_now()
        (messages.success if ok else messages.error)(request, message)
        return _back(request)


class PreviewView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Shows the exact JSON that would be sent — the transparency promise."""

    def get(self, request):
        # A throw-away id keeps this preview from creating a persistent identifier.
        install_id = services.get_setting(services.KEY_INSTALL_ID, "") or "(gerado ao ativar)"
        payload = json.dumps(services.build_payload(install_id), indent=2, ensure_ascii=False)
        return HttpResponse(
            '<pre class="text-xs p-3 rounded overflow-x-auto" style="background: var(--ik-paper-2, #f3ece0);">'
            f"{escape(payload)}</pre>"
        )
