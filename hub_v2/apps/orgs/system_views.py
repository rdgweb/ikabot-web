"""N-203: Organizações — the system's administrator over every organization."""
from datetime import datetime, time

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from . import system
from .models import Organization


class SystemOnly(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        from core.access import platform_admin
        if request.user.is_authenticated and not platform_admin(request.user):
            raise Http404()
        return super().dispatch(request, *args, **kwargs)


def _base(request) -> str:
    return "base_partial.html" if request.headers.get("HX-Request") == "true" else "base.html"


class OrganizationsView(SystemOnly):
    def get(self, request):
        from apps.billing.services import billing_open

        rows = [system.overview(org) for org in Organization._base_manager.order_by("-created_at")]
        return render(request, "orgs/system_list.html", {"rows": rows, "base_template": _base(request),
                                                         "billing_open": billing_open()})

    def post(self, request):
        """N-208: open or close subscriptions for every customer."""
        from apps.audit.services import record
        from apps.billing.services import set_billing_open

        on = request.POST.get("action") == "billing_open"
        set_billing_open(on)
        record("billing.subscriptions_opened" if on else "billing.subscriptions_closed", target_label="Assinaturas")
        messages.success(request, "Assinaturas abertas para os clientes." if on else "Assinaturas fechadas: o plano sumiu das telas dos clientes.")
        return redirect("system-organizations")


class OrganizationView(SystemOnly):
    def get(self, request, pk):
        org = get_object_or_404(Organization._base_manager, pk=pk)
        return render(request, "orgs/system_detail.html", {"row": system.overview(org), "base_template": _base(request),
                                                           "today": timezone.localdate()})

    def post(self, request, pk):
        from apps.accounts.models import Account
        from apps.billing.services import account_for, set_agent_addon, set_proxy_addon

        org = get_object_or_404(Organization._base_manager, pk=pk)
        action = request.POST.get("action", "")
        try:
            if action == "extend":
                days = max(1, min(365, int(request.POST.get("days") or 7)))
                system.extend_trial(org, days)
                messages.success(request, f"Teste estendido em {days} dias.")
            elif action in ("exempt", "unexempt"):
                system.set_exempt(org, action == "exempt")
                messages.success(request, "Organização isenta." if action == "exempt" else "Isenção retirada.")
            elif action == "courtesy":
                raw = request.POST.get("until") or ""
                until = timezone.make_aware(datetime.combine(datetime.strptime(raw, "%Y-%m-%d").date(), time(23, 59)))
                system.set_courtesy(org, until)
                messages.success(request, f"Proxies e agente da plataforma sem cobrança até {until:%d/%m/%Y}.")
            elif action == "courtesy_off":
                system.set_courtesy(org, None)
                messages.success(request, "Cortesia retirada.")
            elif action in ("pause", "resume"):
                system.set_active(org, action == "resume")
                messages.success(request, "Organização reativada." if action == "resume" else "Organização pausada.")
            elif action in ("proxies_on", "proxies_off", "agent_on", "agent_off"):
                from .services import DEFAULT_SLUG

                if org.slug == DEFAULT_SLUG:
                    raise ValueError("A organização da plataforma usa os próprios nós e proxies: não tem adicionais.")
                account = get_object_or_404(Account._base_manager, pk=request.POST.get("account"), organization=org)
                on = action.endswith("_on")
                handler = set_proxy_addon if action.startswith("proxies") else set_agent_addon
                outcome = handler(account_for(org), account, on)
                if outcome.get("ok"):
                    from apps.audit.services import record

                    record(f"organization.{action}", target=account, target_label=account.label)
                    messages.success(request, f"{account.label}: {'ligado' if on else 'desligado'}.")
                else:
                    messages.error(request, f"{account.label}: {outcome.get('reason')}")
            elif action == "delete":
                if (request.POST.get("confirm_name") or "").strip() != org.name:
                    messages.error(request, "Para apagar, digite o nome exato da organização.")
                    return redirect("system-organization", pk=org.pk)
                done = system.delete_organization(org)
                messages.success(request, f"Organização {done['name']} apagada: {done['accounts']} conta(s); "
                                          f"{done['users_switched_off']} usuário(s) desligado(s).")
                return redirect("system-organizations")
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect("system-organization", pk=org.pk)
