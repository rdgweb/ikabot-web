"""Templos e Fé (N-141): every island with one of our temples — the miracle, the faith and our priests in every
account — and the two things to do about it: balance the islands to 100% faith, or read the temples again."""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView

from apps.accounts.models import GameAccount
from apps.game.services import miracle_rules
from apps.orgs.views import can_change
from apps.game.services.temples import balance_jobs, islands_for_organization, plan_faith, projected_faith


def _reserve(value) -> int:
    """Free citizens each city keeps out of the priests (they keep paying gold)."""
    try:
        return max(0, min(100000, int(value or 0)))
    except (TypeError, ValueError):
        return 0


class TemplesView(LoginRequiredMixin, TemplateView):
    template_name = "game/temples.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        reserve = _reserve(self.request.GET.get("reserva"))
        islands = islands_for_organization()
        cards = []
        for island in islands:
            planned = plan_faith(island, reserve=reserve)
            changes = [t for t in island.own if t.target is not None and t.target != t.priests]
            cards.append({
                "short": island.short_priests if planned else 0,
                "citizens_used": sum(t.citizens_used for t in island.own),
                "free": sum(t.free_citizens for t in island.own if t.max_priests),
                "island": island,
                "planned": planned,
                "changes": changes,
                "delta": sum(t.target - t.priests for t in changes),
                "projected": projected_faith(island) if planned else None,
                "others_pct": min(100, island.others_share + island.base_share),
                "own_pct": max(0, min(100 - min(100, island.others_share + island.base_share), island.own_share)),
            })
        ctx["cards"] = cards
        ctx["reserve"] = reserve
        ctx["rules"] = miracle_rules.rules_for(getattr(self.request, "organization", None))
        ctx["can_change_rules"] = can_change(self.request)
        ctx["short_islands"] = sum(1 for c in cards if c["short"])
        ctx["to_balance"] = sum(1 for c in cards if c["changes"])
        ctx["accounts"] = len({t.game_account_id for i in islands for t in i.own})
        return ctx

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")
        if action == "rules":
            if not can_change(request) or getattr(request, "organization", None) is None:
                raise Http404()
            miracle_rules.save_rules(request.organization, request.POST)
            messages.success(request, "Regras dos milagres salvas.")
        elif action == "balance":
            key = request.POST.get("island") or ""
            reserve = _reserve(request.POST.get("reserva"))
            islands = [i for i in islands_for_organization() if not key or i.key == key]
            created = balance_jobs(islands, reserve=reserve, created_by=request.user)
            if created:
                messages.success(request, f"Equilíbrio pedido: {created} job{'s' if created != 1 else ''} de sacerdotes.")
            else:
                messages.info(request, "Nada a mudar: as ilhas já estão no ponto.")
        elif action == "refresh":
            from apps.jobs.services.workflows import create_job_with_workflow

            created = 0
            account_ids = {t.game_account_id for i in islands_for_organization() for t in i.own}
            for ga in GameAccount.objects.filter(pk__in=account_ids).select_related("account__node"):
                create_job_with_workflow(account=ga.account, game_account=ga, node=ga.account.node, action_code=35,
                                         inputs={"target_mode": "keep"}, status="queued", created_by=request.user,
                                         trigger_type="manual")
                created += 1
            messages.success(request, f"Leitura dos templos pedida para {created} conta{'s' if created != 1 else ''}.")
        if action == "balance" and _reserve(request.POST.get("reserva")):
            return redirect(f"{reverse('game:temples')}?reserva={_reserve(request.POST.get('reserva'))}")
        return redirect("game:temples")
