"""Registro de atividades (N-120): the owner reads the trail of their organization."""
from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
from django.views.generic import TemplateView

from .models import AuditEvent
from .services import ACTIONS, CATEGORIES

PERIODS = {"1": 1, "7": 7, "30": 30, "90": 90, "365": 365}
END_REASONS = {"closed": "fechado por quem usava", "expired": "tempo esgotado", "error": "erro", "missing": "sessão não encontrada"}


def _without_page(params) -> str:
    query = params.copy()
    query.pop("page", None)
    return query.urlencode()


def visible_events(user):
    """Superusers see the whole platform; an owner sees the organizations they own."""
    if user.is_superuser:
        return AuditEvent.objects.all()
    owned = user.memberships.filter(role="owner").values_list("organization_id", flat=True)
    return AuditEvent.objects.filter(organization_id__in=list(owned))


class AuditListView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "audit/list.html"

    def test_func(self):
        user = self.request.user
        return user.is_superuser or user.memberships.filter(role="owner").exists()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET
        events = visible_events(self.request.user).select_related("organization")
        period = params.get("periodo", "7")
        if period in PERIODS:
            events = events.filter(created_at__gte=timezone.now() - timedelta(days=PERIODS[period]))
        category = params.get("categoria", "")
        if category in CATEGORIES:
            events = events.filter(category=category)
        who = params.get("quem", "").strip()
        if who:
            events = events.filter(actor_label__iexact=who)
        text = params.get("busca", "").strip()
        if text:
            events = events.filter(Q(target_label__icontains=text) | Q(actor_label__icontains=text) | Q(ip__startswith=text))
        failed = params.get("falhas") == "1"
        if failed:
            events = events.exclude(result="ok")

        page = Paginator(events, 50).get_page(params.get("page"))
        for event in page:
            event.label = ACTIONS.get(event.action, ("", event.action))[1]
            event.category_label = CATEGORIES.get(event.category, event.category)
            details = event.details or {}
            event.note = END_REASONS.get(details.get("reason"), details.get("reason") or "")
            if details.get("contas"):
                event.note = ", ".join(details["contas"])
            elif details.get("campos"):
                event.note = ", ".join(details["campos"])
        ctx.update({
            "page": page,
            "categories": CATEGORIES,
            "people": visible_events(self.request.user).exclude(actor_label="").values_list("actor_label", flat=True).distinct().order_by("actor_label")[:50],
            "filters": {"periodo": period, "categoria": category, "quem": who, "busca": text, "falhas": failed},
            "querystring": _without_page(params),
        })
        return ctx
