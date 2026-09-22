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
    """Acknowledge the first-use notice ("Entendi"), turn telemetry back on, or switch it off."""

    def post(self, request):
        if services.kill_switch_on():
            messages.info(request, "A telemetria está desligada por configuração do ambiente (TELEMETRY_DISABLED).")
        elif request.POST.get("choice") == "decline":
            services.decline()
            messages.info(request, "Telemetria desativada. Nada será enviado.")
        else:
            services.enable()
            ok, message = services.send_now()
            messages.success(
                request,
                "Telemetria ativa. Você pode escolher o que é enviado em Configurações → Telemetria."
                + ("" if ok else f" (primeiro envio pendente: {message})"),
            )
        return _back(request)


class ItemsView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Save which optional items are sent; applies immediately (an off item is dropped server-side)."""

    def post(self, request):
        if services.kill_switch_on():
            messages.info(request, "A telemetria está desligada por configuração do ambiente (TELEMETRY_DISABLED).")
            return _back(request)
        selected = set(request.POST.getlist("items")) & set(services.ITEM_KEYS)
        services.save_items(selected)
        services.enable()
        ok, message = services.send_now()
        messages.success(
            request,
            "Preferências de telemetria salvas."
            + (" O servidor já recebeu o resumo atualizado." if ok else f" (envio pendente: {message})"),
        )
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
        # Do not create a persistent identifier just to preview.
        install_id = services.get_setting(services.KEY_INSTALL_ID, "") or "(gerado no primeiro envio)"
        payload = json.dumps(services.build_payload(install_id), indent=2, ensure_ascii=False)
        note = (
            "O servidor também registra o IP de origem (por até 90 dias)."
            if "ip" in services.enabled_items()
            else "IP desligado: o servidor não registra o IP."
        )
        return HttpResponse(
            '<pre class="text-xs p-3 rounded overflow-x-auto" style="background: var(--ik-paper-2, #f3ece0);">'
            f"{escape(payload)}</pre><p class=\"text-xs text-muted mt-1\">{note}</p>"
        )
