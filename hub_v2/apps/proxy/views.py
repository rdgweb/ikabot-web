"""
Views para o app Proxy — CRUD, sincronização e teste de proxies.
"""

import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, UpdateView, ListView

from .forms import ProxyForm
from .models import ProxyProfile


# ── Helpers ─────────────────────────────────────────────────────────────────

def _htmx_toast(message: str, kind: str = "success") -> str:
    return json.dumps({"toast": {"type": kind, "message": message}})


def _build_queryset(request):
    qs = ProxyProfile.objects.select_related("assigned_node").order_by("address")

    status = request.GET.get("status", "")
    if status == "active":
        qs = qs.filter(active=True)
    elif status == "inactive":
        qs = qs.filter(active=False)

    test_result = request.GET.get("test_result", "")
    if test_result == "ok":
        qs = qs.filter(last_test_status=True)
    elif test_result == "fail":
        qs = qs.filter(last_test_status=False)
    elif test_result == "untested":
        qs = qs.filter(last_test_status__isnull=True)

    assigned = request.GET.get("assigned", "")
    if assigned == "yes":
        qs = qs.filter(assigned_node__isnull=False)
    elif assigned == "no":
        qs = qs.filter(assigned_node__isnull=True)

    country = request.GET.get("country", "").strip().upper()
    if country:
        qs = qs.filter(country_code__iexact=country)

    return qs


# ── List / Table ─────────────────────────────────────────────────────────────

class ProxyListView(LoginRequiredMixin, ListView):
    model = ProxyProfile
    template_name = "proxy/proxy_list.html"
    context_object_name = "object_list"
    paginate_by = 25

    def get_queryset(self):
        return _build_queryset(self.request)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_status"] = self.request.GET.get("status", "")
        ctx["filter_test_result"] = self.request.GET.get("test_result", "")
        ctx["filter_assigned"] = self.request.GET.get("assigned", "")
        ctx["filter_country"] = self.request.GET.get("country", "")
        ctx["country_choices"] = (
            ProxyProfile.objects.exclude(country_code="")
            .values_list("country_code", flat=True)
            .distinct()
            .order_by("country_code")
        )
        return ctx


class ProxyTableView(LoginRequiredMixin, ListView):
    """GET /proxy/table/ — returns just the table partial for HTMX refresh."""
    model = ProxyProfile
    template_name = "proxy/partials/proxy_table.html"
    context_object_name = "object_list"
    paginate_by = 25

    def get_queryset(self):
        return _build_queryset(self.request)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_status"] = self.request.GET.get("status", "")
        ctx["filter_test_result"] = self.request.GET.get("test_result", "")
        ctx["filter_assigned"] = self.request.GET.get("assigned", "")
        ctx["filter_country"] = self.request.GET.get("country", "")
        return ctx


# ── CRUD ─────────────────────────────────────────────────────────────────────

class ProxyCreateView(LoginRequiredMixin, CreateView):
    model = ProxyProfile
    form_class = ProxyForm
    template_name = "proxy/proxy_form.html"
    success_url = reverse_lazy("proxy:list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = "Novo Proxy"
        ctx["form_subtitle"] = "Cadastrar proxy manual ou externo"
        return ctx


class ProxyEditView(LoginRequiredMixin, UpdateView):
    model = ProxyProfile
    form_class = ProxyForm
    template_name = "proxy/proxy_form.html"
    success_url = reverse_lazy("proxy:list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = "Editar Proxy"
        ctx["form_subtitle"] = f"{self.object.address}:{self.object.port}"
        return ctx


class ProxyDeleteView(LoginRequiredMixin, View):
    """POST: delete proxy. Clears Node.proxy if assigned."""

    def post(self, request, pk):
        proxy = get_object_or_404(ProxyProfile, pk=pk)
        node = proxy.assigned_node
        if node:
            node.proxy = ""
            node.save(update_fields=["proxy"])
        label = f"{proxy.address}:{proxy.port}"
        proxy.delete()

        resp = HttpResponse(status=204)
        resp["HX-Trigger"] = _htmx_toast(f"Proxy {label} excluído.")
        resp["HX-Redirect"] = str(reverse_lazy("proxy:list"))
        return resp


class ProxyToggleView(LoginRequiredMixin, View):
    """POST: toggle active status of a proxy."""

    def post(self, request, pk):
        proxy = get_object_or_404(ProxyProfile, pk=pk)
        proxy.active = not proxy.active
        proxy.save(update_fields=["active"])

        label = "ativado" if proxy.active else "desativado"
        resp = HttpResponse(status=204)
        resp["HX-Trigger"] = json.dumps({
            "toast": {"type": "success", "message": f"Proxy {proxy.address}:{proxy.port} {label}."},
            "proxyTableRefresh": True,
        })
        return resp


# ── Test single ───────────────────────────────────────────────────────────────

class ProxyTestView(LoginRequiredMixin, View):
    """POST /proxy/<pk>/test/ — tests one proxy and returns the updated <tr>."""

    def post(self, request, pk):
        from .services import test_proxy

        proxy = get_object_or_404(ProxyProfile, pk=pk)
        result = test_proxy(proxy)

        if result["success"]:
            toast_msg = f"Proxy OK — IP: {result['ip']} ({result['latency_ms']}ms)"
            toast_type = "success"
        else:
            toast_msg = f"Proxy falhou: {result['error']}"
            toast_type = "error"

        html = render_to_string(
            "proxy/partials/proxy_row.html",
            {"proxy": proxy},
            request=request,
        )
        resp = HttpResponse(html, content_type="text/html")
        resp["HX-Trigger"] = _htmx_toast(toast_msg, toast_type)
        return resp


# ── Test all ──────────────────────────────────────────────────────────────────

class ProxyTestAllView(LoginRequiredMixin, View):
    """POST /proxy/test-all/ — tests all active proxies in parallel."""

    def post(self, request):
        from .services import test_all_proxies

        result = test_all_proxies(auto_inactivate=True, auto_reassign=True)

        parts = [f"{result['ok']}/{result['tested']} OK"]
        if result["failed"]:
            parts.append(f"{result['failed']} falhas")
        if result["inactivated"]:
            parts.append(f"{result['inactivated']} inativados")
        if result["reassigned"]:
            parts.append(f"{result['reassigned']} nós reatribuídos")

        kind = "success" if result["failed"] == 0 else ("warning" if result["ok"] > 0 else "error")
        msg = "Teste concluído — " + ", ".join(parts) + "."

        resp = HttpResponse(status=200)
        resp["HX-Trigger"] = json.dumps({
            "toast": {"type": kind, "message": msg},
            "proxyTableRefresh": True,
        })
        return resp


# ── Sync ──────────────────────────────────────────────────────────────────────

class ProxySyncView(LoginRequiredMixin, View):
    """POST /proxy/sync/ — sync proxies from Webshare.io."""

    def post(self, request, *args, **kwargs):
        from .services import sync_webshare

        result = sync_webshare()

        if result["success"]:
            parts = []
            if result["created"]:
                parts.append(f"{result['created']} criados")
            if result["updated"]:
                parts.append(f"{result['updated']} atualizados")
            if result.get("rotated"):
                parts.append(f"{result['rotated']} rotacionados")
            if result["deactivated"]:
                parts.append(f"{result['deactivated']} desativados")
            detail = ", ".join(parts) if parts else "nenhuma alteração"
            msg = f"Sincronização concluída — {result['total']} proxies ({detail})."
            kind = "success"
        else:
            msg = f"Sincronização falhou: {result['error']}"
            kind = "error"

        resp = HttpResponse(status=200)
        resp["HX-Trigger"] = json.dumps({
            "toast": {"type": kind, "message": msg},
            "proxyTableRefresh": True,
        })
        return resp
