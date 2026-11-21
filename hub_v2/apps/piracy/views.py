"""Pirataria (N-210/N-211): the organization's piracy on one server and the month's champions."""
from collections import Counter

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import redirect, render
from django.views import View

from . import services


def _base(request) -> str:
    return "base_partial.html" if request.headers.get("HX-Request") == "true" else "base.html"


def _can_run(request) -> bool:
    from apps.orgs.models import Membership
    from apps.orgs.services import role_of

    if request.user.is_superuser:
        return True
    organization = getattr(request, "organization", None)
    return role_of(request.user, organization) in (Membership.OWNER, Membership.OPERATOR) if organization else False


def _server(request) -> str:
    from apps.accounts.models import GameAccount

    servers = services.servers()
    chosen = request.GET.get("server") or request.POST.get("server") or ""
    if chosen in servers:
        return chosen
    counts = Counter(GameAccount.objects.filter(active=True).values_list("server_id", flat=True))
    return counts.most_common(1)[0][0] if counts else ""


class PiracyView(LoginRequiredMixin, View):
    def get(self, request):
        server = _server(request)
        data = services.overview(server) if server else {"rows": [], "ranking": [], "raids": []}
        month = data.get("month")
        weeks = []
        if month:
            by_week = {}
            for route in month.routes.select_related("raider__account", "target__account").order_by("week", "stage", "group"):
                by_week.setdefault(route.week, []).append(route)
            weeks = [{"week": w, "routes": rs, "done": sum(1 for r in rs if r.status == "done")} for w, rs in sorted(by_week.items())]
        history = []
        from .models import PiracyMonth

        for past in PiracyMonth.objects.filter(server_id=server).prefetch_related("champions__game_account")[:12]:
            history.append({"month": past, "champions": [c.game_account for c in past.champions.all()]})
        clock = data.get("clock")
        clock_human = ""
        if clock is not None:
            days, rest = divmod(int(clock), 86400)
            clock_human = (f"{days}d {rest // 3600}h" if days else f"{rest // 3600}h {(rest % 3600) // 60:02d}min")
        able = {ga.pk for ga in services.eligible(server)} if server else set()
        for row in data.get("rows", []):
            row["eligible"] = row["ga"].pk in able
        return render(request, "piracy/index.html", {
            **data, "server": server, "clock_human": clock_human, "eligible_count": len(able), "servers": services.servers(), "weeks": weeks, "history": history,
            "can_run": _can_run(request), "base_template": _base(request),
            "champions": [c.game_account for c in month.champions.select_related("game_account__account")] if month else [],
        })

    def post(self, request):
        from apps.audit.services import record

        if not _can_run(request):
            raise Http404()
        server = _server(request)
        action = request.POST.get("action", "")
        try:
            if action in ("plan", "choose"):
                champions = None
                if action == "choose":
                    ids = request.POST.getlist("champion")
                    pool = {str(ga.pk): ga for ga in services.accounts_on(server)}
                    champions = [pool[i] for i in ids if i in pool]
                month = services.plan_month(request.organization, server, champions=champions)
                names = ", ".join(c.game_account.name for c in month.champions.select_related("game_account"))
                record("piracy.month_planned", target=month, target_label=f"{server}: {names}")
                messages.success(request, f"Campeãs do mês: {names}.")
            elif action == "close":
                month = services.current_month(server)
                if month:
                    services.close_month(month, "encerrado pelo usuário")
                    record("piracy.month_closed", target=month, target_label=server)
                    messages.success(request, "Plano do mês encerrado; o que estava planejado foi pulado.")
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect(f"/pirataria/?server={server}")
