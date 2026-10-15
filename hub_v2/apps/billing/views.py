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


def _public_url(request, path: str) -> str:
    """N-126: behind the proxy the request looks like http; the Asaas only accepts the https address."""
    base = (settings.SITE_URL or settings.HUB_PUBLIC_URL or "").rstrip("/")
    if not base:
        base = request.build_absolute_uri("/").rstrip("/").replace("http://", "https://", 1)
    return base + path


def _monthly(account):
    from .services import monthly_value

    return monthly_value(account)


def _addons(organization) -> int:
    from apps.proxy.platform import accounts_with_addon

    return accounts_with_addon(organization)


def _leave_to(request, url: str):
    """N-126: the Asaas page is another site — a boosted (HTMX) request must leave with a full page load,
    or the browser follows the redirect inside XHR and CORS blocks it."""
    if request.headers.get("HX-Request") == "true":
        from django.http import HttpResponse

        response = HttpResponse(status=204)
        response["HX-Redirect"] = url
        return response
    return redirect(url)


def _org_tabs(request, tab: str) -> dict:
    """The header of Minha organização (N-126)."""
    from apps.orgs.services import role_of

    role = Membership.OWNER if request.user.is_superuser else role_of(request.user, request.organization)
    return {"organization": request.organization, "tab": tab, "role": role}


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
        subscribed = bool(account.subscription_id and account.status == BillingAccount.ACTIVE and account.paid_until)
        from .services import upgrade_cost
        ctx.update({
            "account": account, "state": state, "active": active, "can_change": _is_owner(self.request),
            "configured": asaas.configured(), "sandbox": settings.ASAAS_SANDBOX,
            "options": [{"limit": limit, "price": plans.brl(plans.price_for(limit)),
                         "fits": active <= limit or (subscribed and limit < account.limit),
                         "current": limit == current, "suggested": limit == plans.tier_for(active),
                         "now": plans.brl(upgrade_cost(account, limit)) if subscribed and limit > account.limit else "",
                         "later": subscribed and limit < account.limit}
                        for limit in plans.limits()[:7]],
            "subscribed": subscribed,
            "upgrade_value": plans.brl(account.upgrade_value) if account.upgrade_payment_id else "",
            "monthly": plans.brl(_monthly(account)) if subscribed else "",
            "addons": _addons(self.request.organization),
            "more": [{"limit": limit, "price": plans.brl(plans.price_for(limit))} for limit in plans.limits()[7:]],
            "price": plans.brl(account.price) if account.price else "",
            "returned": self.request.GET.get("ok") == "1",
            **_org_tabs(self.request, "plan"),
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
            elif action == "keep":
                from .services import change_tier

                change_tier(account, request.organization, account.limit)
                messages.success(request, f"Faixa mantida: até {account.limit} subcontas.")
            else:
                limit = int(request.POST.get("limit") or 0)
                if limit not in plans.limits():
                    raise ValueError
                if account.subscription_id and account.status == BillingAccount.ACTIVE:
                    response = self._change(request, account, limit)
                    if response is not None:
                        return response
                elif limit < active_subaccounts(request.organization):
                    messages.error(request, f"Você tem mais de {limit} subcontas ativas. Pause algumas antes de escolher esta faixa.")
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
            return _leave_to(request, account.link_url)
        if account.link_id:
            try:
                asaas.delete_payment_link(account.link_id)
            except asaas.AsaasError:
                pass                                                    # an old link left behind is harmless
        price = plans.price_for(limit)
        link_id, url = asaas.create_payment_link(
            organization=request.organization, limit=limit, value=price,
            success_url=_public_url(request, "/organizacao/plano/?ok=1"),
        )
        account.link_id, account.link_url, account.link_limit = link_id, url, limit
        account.save(update_fields=["link_id", "link_url", "link_limit", "updated_at"])
        record("billing.checkout", target=request.organization, target_label=f"até {limit} subcontas · {plans.brl(price)}")
        return _leave_to(request, url)

    def _change(self, request, account, limit):
        """N-124: up = pay the prorated difference first; down = from the next due date."""
        from django.utils import timezone

        from apps.audit.services import record

        from .services import change_tier

        old = account.limit
        outcome = change_tier(account, request.organization, limit)
        price = plans.brl(plans.price_for(limit))
        if outcome["kind"] == "kept":
            messages.success(request, f"Faixa mantida: até {limit} subcontas.")
            return None
        if outcome["kind"] == "down":
            when = timezone.localtime(outcome["when"]).strftime("%d/%m")
            record("billing.changed", target=request.organization, target_label=f"{old} → {limit} subcontas em {when} · {price}")
            messages.success(request, f"A faixa passa para até {limit} subcontas ({price}/mês) no vencimento de {when}. "
                                      f"Até lá continua a atual.")
            return None
        if outcome["kind"] == "up-free":
            record("billing.changed", target=request.organization, target_label=f"{old} → {limit} subcontas · {price}")
            messages.success(request, f"Faixa agora é até {limit} subcontas; a próxima cobrança já vem com {price}.")
            return None
        record("billing.changed", target=request.organization,
               target_label=f"{old} → {limit} subcontas · diferença {plans.brl(outcome['cost'])}")
        return _leave_to(request, outcome["url"])

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
