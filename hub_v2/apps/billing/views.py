"""Minha organização > Plano (N-124) and the Asaas notices."""
import hmac
import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView

from apps.orgs.models import Membership

from . import asaas, plans
from .models import BillingAccount
from .services import account_for, active_subaccounts, handle_event, state_of

logger = logging.getLogger(__name__)


def _is_owner(request) -> bool:
    if request.user.is_superuser:
        return True
    return request.user.memberships.filter(organization=request.organization, role=Membership.OWNER).exists()


class PlanView(LoginRequiredMixin, TemplateView):
    """GET/POST /organizacao/plano/ — the organization's plan: choose, subscribe, change, cancel."""

    template_name = "billing/plan.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and getattr(request, "organization", None) is None:
            raise Http404()
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        account = account_for(self.request.organization)
        state = state_of(account)
        active = active_subaccounts(self.request.organization)
        current = account.limit if account.status in (BillingAccount.ACTIVE, BillingAccount.CANCELED) else None
        ctx.update({
            "account": account, "state": state, "active": active, "can_change": _is_owner(self.request),
            "configured": asaas.configured(), "sandbox": settings.ASAAS_SANDBOX,
            "options": [{"limit": limit, "price": plans.brl(plans.price_for(limit)), "fits": active <= limit,
                         "current": limit == current, "suggested": limit == plans.tier_for(active)}
                        for limit in plans.limits()[:7]],
            "more": [{"limit": limit, "price": plans.brl(plans.price_for(limit))} for limit in plans.limits()[7:]],
            "price": plans.brl(account.price) if account.price else "",
            "returned": self.request.GET.get("ok") == "1",
        })
        return ctx

    def post(self, request):
        if not _is_owner(request):
            raise Http404()
        account = account_for(request.organization)
        action = request.POST.get("action")
        try:
            if action == "cancel":
                self._cancel(request, account)
            else:
                limit = int(request.POST.get("limit") or 0)
                if limit not in plans.limits():
                    raise ValueError
                if limit < active_subaccounts(request.organization):
                    messages.error(request, f"Você tem mais de {limit} subcontas ativas. Pause algumas antes de escolher esta faixa.")
                elif account.subscription_id and account.status == BillingAccount.ACTIVE:
                    self._change(request, account, limit)
                else:
                    return self._subscribe(request, account, limit)
        except ValueError:
            messages.error(request, "Escolha uma das faixas.")
        except asaas.AsaasError as exc:
            messages.error(request, str(exc))
        return redirect("billing:plan")

    def _subscribe(self, request, account, limit):
        from apps.audit.services import record

        if account.link_id and account.link_limit == limit and account.link_url:
            return redirect(account.link_url)
        if account.link_id:
            try:
                asaas.delete_payment_link(account.link_id)
            except asaas.AsaasError:
                pass                                                    # an old link left behind is harmless
        price = plans.price_for(limit)
        link_id, url = asaas.create_payment_link(
            organization=request.organization, limit=limit, value=price,
            success_url=request.build_absolute_uri("/organizacao/plano/?ok=1"),
        )
        account.link_id, account.link_url, account.link_limit = link_id, url, limit
        account.save(update_fields=["link_id", "link_url", "link_limit", "updated_at"])
        record("billing.checkout", target=request.organization, target_label=f"até {limit} subcontas · {plans.brl(price)}")
        return redirect(url)

    def _change(self, request, account, limit):
        from apps.audit.services import record

        price = plans.price_for(limit)
        asaas.update_subscription_value(account.subscription_id, price, f"IkaHub — até {limit} subcontas")
        old = account.limit
        account.limit, account.price, account.link_limit = limit, price, limit
        account.save(update_fields=["limit", "price", "link_limit", "updated_at"])
        record("billing.changed", target=request.organization, target_label=f"{old} → {limit} subcontas · {plans.brl(price)}")
        messages.success(request, f"Plano alterado para até {limit} subcontas ({plans.brl(price)}/mês) a partir da próxima cobrança.")

    def _cancel(self, request, account):
        from apps.audit.services import record

        if not account.subscription_id or account.status != BillingAccount.ACTIVE:
            return
        asaas.cancel_subscription(account.subscription_id)
        account.status = BillingAccount.CANCELED
        account.save(update_fields=["status", "updated_at"])
        record("billing.canceled", target=request.organization, target_label=request.organization.name)
        messages.success(request, "Assinatura cancelada. O plano vale até o fim do período já pago.")


@csrf_exempt
def asaas_webhook(request):
    """POST /api/billing/asaas/ — authenticated by the token set on the webhook; always quick."""
    token = settings.ASAAS_WEBHOOK_TOKEN
    if request.method != "POST" or not token:
        raise Http404()
    given = request.META.get("HTTP_ASAAS_ACCESS_TOKEN", "")
    if not hmac.compare_digest(given.encode(), token.encode()):
        logger.warning("Asaas webhook with a wrong token")
        return JsonResponse({"error": "unauthorized"}, status=401)
    if len(request.body) > 65536:
        return JsonResponse({"error": "too large"}, status=413)
    try:
        payload = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "invalid"}, status=400)
    result = handle_event(payload if isinstance(payload, dict) else {})
    return JsonResponse({"received": True, "result": result})
