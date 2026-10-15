"""Minha organização > Proxies (N-129): the customer's own proxies and the platform-proxies add-on per account."""
import re
from concurrent.futures import ThreadPoolExecutor

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect

from .models import Membership
from .owner_views import OrgPage

MAX_PASTE = 50
LINE = re.compile(
    r"^(?:https?://)?(?:(?P<u1>[^:@\s]+):(?P<p1>[^@\s]+)@)?(?P<host>[A-Za-z0-9.\-]+):(?P<port>\d{1,5})(?::(?P<u2>[^:\s]+):(?P<p2>\S+))?$"
)


def parse_lines(text: str) -> tuple[list[dict], list[str]]:
    """'host:port', 'host:port:user:pass', 'user:pass@host:port' or 'http://user:pass@host:port'."""
    good, bad = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = LINE.match(line)
        port = int(match.group("port")) if match else 0
        if not match or not 0 < port < 65536:
            bad.append(line[:60])
            continue
        good.append({"address": match.group("host"), "port": port,
                     "username": match.group("u1") or match.group("u2") or "",
                     "password": match.group("p1") or match.group("p2") or ""})
    return good, bad


def _test_many(proxies) -> None:
    from apps.proxy.services import test_proxy

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(test_proxy, proxies))


class ProxiesView(OrgPage):
    template_name = "orgs/proxies.html"
    tab = "proxies"

    def get_context_data(self, **kwargs):
        from apps.accounts.models import Account
        from apps.billing import plans
        from apps.billing.services import account_for, subscribed
        from apps.proxy import platform
        from apps.proxy.models import AccountProxyReservation, ProxyProfile

        ctx = super().get_context_data(**kwargs)
        organization = self.request.organization
        own = list(ProxyProfile.objects.filter(lent_to__isnull=True).select_related("assigned_node")
                   .prefetch_related("account_reservation__account").order_by("address", "port"))
        rows = []
        for account in Account.objects.select_related("node").order_by("label"):
            reserved = [r.proxy_profile for r in AccountProxyReservation.objects.filter(account=account).select_related("proxy_profile")]
            rows.append({
                "account": account, "total": len(reserved),
                "ok": sum(1 for proxy in reserved if proxy.active and proxy.last_test_status is True),
                "lent": sum(1 for proxy in reserved if proxy.lent_to_id == account.pk),
            })
        billing = account_for(organization)
        ctx.update({
            "own": own, "own_ok": sum(1 for proxy in own if proxy.active and proxy.last_test_status is True),
            "rows": rows, "addon_price": plans.brl(plans.ADDON_PROXIES), "subscribed": subscribed(billing),
            "available": platform.accounts_available(), "is_platform": billing.status == billing.EXEMPT,
        })
        return ctx

    def post(self, request):
        from apps.audit.services import record

        if self.role not in (Membership.OWNER, Membership.OPERATOR):
            raise Http404()
        action = request.POST.get("action")
        if action == "add":
            self._add(request)
        elif action == "test":
            from apps.proxy.models import ProxyProfile

            _test_many(list(ProxyProfile.objects.all()[:80]))
            messages.success(request, "Proxies testados.")
        elif action == "remove":
            self._remove(request)
        elif action == "addon":
            if self.role != Membership.OWNER:
                raise Http404()
            self._addon(request, record)
        return redirect("orgs:proxies")

    def _add(self, request):
        from apps.audit.services import record
        from apps.proxy.models import ProxyProfile
        from core.encryption import encrypt

        good, bad = parse_lines(request.POST.get("lines", ""))
        if len(good) > MAX_PASTE:
            messages.error(request, f"Cole no máximo {MAX_PASTE} proxies por vez.")
            return
        created = []
        for item in good:
            if ProxyProfile.objects.filter(address=item["address"], port=item["port"], username=item["username"]).exists():
                continue
            created.append(ProxyProfile.objects.create(
                organization=request.organization, provider=ProxyProfile.Provider.MANUAL,
                address=item["address"], port=item["port"], username=item["username"],
                password_enc=encrypt(item["password"]) if item["password"] else "",
            ))
        if created:
            _test_many(created)
            record("proxyprofile.created", target=created[0], target_label=f"{len(created)} proxy(s)")
        ok = sum(1 for proxy in created if ProxyProfile._base_manager.get(pk=proxy.pk).last_test_status)
        text = f"{len(created)} proxy(s) adicionado(s), {ok} respondendo."
        if bad:
            text += f" {len(bad)} linha(s) ignorada(s): formato não reconhecido."
        (messages.success if created else messages.error)(request, text if created or bad else "Nenhum proxy novo.")

    def _remove(self, request):
        from apps.audit.services import record
        from apps.proxy.models import ProxyProfile

        proxy = ProxyProfile.objects.filter(pk=request.POST.get("proxy"), lent_to__isnull=True).select_related("assigned_node").first()
        if proxy is None:
            return
        node = proxy.assigned_node
        if node is not None and node.proxy == proxy.proxy_url:
            node.proxy = ""
            node.save(update_fields=["proxy"])
        label = f"P{proxy.pk}"
        proxy.delete()                                       # its account reservation goes with it
        record("proxyprofile.deleted", target_label=label)
        messages.success(request, "Proxy removido.")

    def _addon(self, request, record):
        from apps.accounts.models import Account
        from apps.billing import asaas
        from apps.billing.services import account_for, set_proxy_addon

        account = Account.objects.filter(pk=request.POST.get("account")).first()
        if account is None:
            raise Http404()
        on = request.POST.get("on") == "1"
        try:
            outcome = set_proxy_addon(account_for(request.organization), account, on)
        except asaas.AsaasError as exc:
            messages.error(request, str(exc))
            return
        if not outcome.get("ok"):
            messages.error(request, outcome["reason"])
            return
        record("account.updated", target=account, target_label=f"{account.label}: proxies da plataforma {'ligados' if on else 'desligados'}")
        if on:
            messages.success(request, f"Proxies da plataforma ligados para {account.label} ({outcome.get('lent', 0)} reservado(s)).")
        elif outcome.get("until"):
            messages.success(request, f"Os proxies da plataforma de {account.label} ficam até {outcome['until']:%d/%m} e depois voltam ao pool.")
        else:
            messages.success(request, f"Proxies da plataforma desligados para {account.label}.")
