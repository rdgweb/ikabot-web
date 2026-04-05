"""
Node CRUD views + deploy page.
"""

import json
from collections import Counter

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import DetailView, CreateView, UpdateView, DeleteView

from core.mixins.views import FilterSortListView
from ..models import Node
from ..filters import NodeFilter
from ..forms import NodeCreateForm, NodeEditForm


def _get_ip_conflicts():
    """Return set of external IPs that appear on more than one active node."""
    ips = (
        Node.objects.filter(active=True)
        .exclude(external_ip="")
        .values_list("external_ip", flat=True)
    )
    counter = Counter(ips)
    return {ip for ip, count in counter.items() if count > 1}


class NodeListView(FilterSortListView):
    model = Node
    filterset_class = NodeFilter
    template_name = "accounts/node_list.html"
    partial_template_name = "accounts/partials/node_table.html"
    paginate_by = 25
    ordering_fields = ["name", "location", "created_at"]
    default_ordering = "name"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["ip_conflicts"] = _get_ip_conflicts()
        return ctx


class NodeDetailView(LoginRequiredMixin, DetailView):
    model = Node
    template_name = "accounts/node_detail.html"
    context_object_name = "node"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["accounts"] = self.object.accounts.all()

        # Proxy profile
        try:
            ctx["proxy_profile"] = self.object.proxy_profile
        except Exception:
            ctx["proxy_profile"] = None

        # IP conflict detection
        node = self.object
        if node.external_ip:
            conflict_nodes = (
                Node.objects.filter(external_ip=node.external_ip, active=True)
                .exclude(pk=node.pk)
            )
            ctx["ip_conflict_nodes"] = list(conflict_nodes)
        else:
            ctx["ip_conflict_nodes"] = []

        return ctx


class NodeCreateView(LoginRequiredMixin, CreateView):
    model = Node
    form_class = NodeCreateForm
    template_name = "accounts/node_create.html"

    def get_success_url(self):
        return reverse_lazy("accounts:node-deploy", kwargs={"pk": self.object.pk})


class NodeEditView(LoginRequiredMixin, UpdateView):
    model = Node
    form_class = NodeEditForm
    template_name = "accounts/node_edit.html"

    def get_success_url(self):
        return reverse_lazy("accounts:node-detail", kwargs={"pk": self.object.pk})


class NodeDeleteView(LoginRequiredMixin, DeleteView):
    model = Node
    success_url = reverse_lazy("accounts:node-list")
    template_name = "accounts/node_confirm_delete.html"


class NodeToggleView(LoginRequiredMixin, View):
    """POST: toggle active status of a node/agent. Returns HTMX toast."""

    def post(self, request, pk):
        node = get_object_or_404(Node, pk=pk)
        node.active = not node.active
        node.save(update_fields=["active"])

        status_label = "ativado" if node.active else "desativado"
        trigger = json.dumps({
            "toast": {
                "type": "success",
                "message": f"Agent {node.name} {status_label}.",
            },
            "nodeToggled": True,
        })
        resp = HttpResponse(status=204)
        resp["HX-Trigger"] = trigger
        return resp


class NodeProxyTestView(LoginRequiredMixin, View):
    """POST: test the proxy assigned to this node. Returns HTMX inline result."""

    def post(self, request, pk):
        node = get_object_or_404(Node, pk=pk)

        try:
            proxy_profile = node.proxy_profile
        except Exception:
            proxy_profile = None

        if not proxy_profile:
            html = (
                '<div class="flex items-center gap-2 px-3 py-2 rounded text-xs mt-3" style="background: #fde8e5;">'
                '<i class="bi bi-x-circle-fill" style="color: var(--ik-bad);"></i>'
                '<span>Nenhum proxy atribuído a este nó.</span>'
                '</div>'
            )
            return HttpResponse(html)

        from apps.proxy.services import test_proxy
        result = test_proxy(proxy_profile)

        if result["success"]:
            html = (
                '<div class="flex items-center gap-2 px-3 py-2 rounded text-xs mt-3" style="background: #e8f2e0;">'
                '<i class="bi bi-check-circle-fill" style="color: var(--ik-good);"></i>'
                f'<span>Proxy OK — IP de saída: <strong class="font-mono">{result["ip"]}</strong> '
                f'({result["latency_ms"]}ms)</span>'
                '</div>'
            )
        else:
            html = (
                '<div class="flex items-center gap-2 px-3 py-2 rounded text-xs mt-3" style="background: #fde8e5;">'
                '<i class="bi bi-x-circle-fill" style="color: var(--ik-bad);"></i>'
                f'<span>Falha: {result["error"]}</span>'
                '</div>'
            )
        return HttpResponse(html)


class NodeDeployView(LoginRequiredMixin, DetailView):
    """Shows deploy token + docker command after node creation."""
    model = Node
    template_name = "accounts/node_deploy.html"
    context_object_name = "node"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Build the hub URL for the deploy command
        request = self.request
        hub_url = f"{request.scheme}://{request.get_host()}"
        ctx["hub_url"] = hub_url
        ctx["deploy_command"] = (
            f"docker run -d --name ikabot-agent \\\n"
            f"  -e HUB_URL={hub_url} \\\n"
            f"  -e AGENT_TOKEN={self.object.deploy_token} \\\n"
            f"  -e AGENT_NODE_ID={self.object.pk} \\\n"
            f"  blackoneal/ikabot-web-agent:latest"
        )
        return ctx
