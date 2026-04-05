"""
Views para o app Proxy — CRUD, sincronização e teste de proxies.
"""

import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, UpdateView, ListView

from .forms import ProxyForm
from .models import ProxyProfile


class ProxyListView(LoginRequiredMixin, ListView):
    model = ProxyProfile
    template_name = "proxy/proxy_list.html"
    context_object_name = "object_list"
    paginate_by = 25

    def get_queryset(self):
        return super().get_queryset().select_related("assigned_node")


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
        proxy.delete()

        trigger = json.dumps({
            "toast": {"type": "success", "message": f"Proxy {proxy.address}:{proxy.port} excluído."},
        })
        resp = HttpResponse(status=204)
        resp["HX-Trigger"] = trigger
        resp["HX-Redirect"] = reverse_lazy("proxy:list")
        return resp


class ProxyToggleView(LoginRequiredMixin, View):
    """POST: toggle active status of a proxy."""

    def post(self, request, pk):
        proxy = get_object_or_404(ProxyProfile, pk=pk)
        proxy.active = not proxy.active
        proxy.save(update_fields=["active"])

        status_label = "ativado" if proxy.active else "desativado"
        trigger = json.dumps({
            "toast": {"type": "success", "message": f"Proxy {proxy.address}:{proxy.port} {status_label}."},
        })
        resp = HttpResponse(status=204)
        resp["HX-Trigger"] = trigger
        return resp


class ProxyTestView(LoginRequiredMixin, View):
    """POST: test proxy connectivity. Returns HTMX toast."""

    def post(self, request, pk):
        from .services import test_proxy

        proxy = get_object_or_404(ProxyProfile, pk=pk)
        result = test_proxy(proxy)

        if result["success"]:
            messages.success(
                request,
                f"Proxy OK — IP: {result['ip']} ({result['latency_ms']}ms)",
            )
        else:
            messages.error(request, f"Proxy falhou: {result['error']}")

        return redirect("proxy:list")


class ProxySyncView(LoginRequiredMixin, View):
    """POST: sync proxies from Webshare.io API."""

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
            messages.success(
                request,
                f"Sincronização concluída — {result['total']} proxies ({detail}).",
            )
        else:
            messages.error(request, f"Sincronização falhou: {result['error']}")
        return redirect("proxy:list")
