"""Pirataria (N-210/N-211): the organization's piracy on one server and the month's champions."""
from collections import Counter

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View

from . import services
from .models import PiracyMember, PiracyMonth, PiracyRoute, PiracyTarget


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


def _clock_human(clock) -> str:
    if clock is None:
        return ""
    days, rest = divmod(int(clock), 86400)
    return f"{days}d {rest // 3600}h" if days else f"{rest // 3600}h {(rest % 3600) // 60:02d}min"


def _best_odds(rows, able) -> list[dict]:
    """The best chances of winning among the accounts (crew against crew), the strongest pairs first."""
    crews = [(r["ga"], r["crew"]) for r in rows if r["ga"].pk in able and r.get("crew") is not None]
    out = []
    for raider, crew in crews:
        for target, other in crews:
            if raider.account_id == target.account_id:
                continue
            chance = services.win_chance(int(crew), int(other))
            if chance is not None:
                out.append({"raider": raider, "target": target, "chance": round(chance * 100)})
    return sorted(out, key=lambda o: -o["chance"])[:6]


class TargetSearchView(LoginRequiredMixin, View):
    """GET /pirataria/alvos/?server=&q= — cities with piracy from Inteligência, to pick a target (HTMX)."""

    def get(self, request):
        server = _server(request)
        rows = services.search_targets(server, request.GET.get("q", "")) if server else []
        return render(request, "piracy/partials/target_results.html", {"rows": rows, "server": server,
                                                                       "can_run": _can_run(request), "q": request.GET.get("q", "")})


class PiracyView(LoginRequiredMixin, View):
    def get(self, request):
        server = _server(request)
        data = services.overview(server) if server else {"rows": [], "ranking": [], "raids": []}
        month = data.get("month")
        able = {ga.pk for ga in services.eligible(server)} if server else set()
        by_ga = {}
        for row in data.get("rows", []):
            row["eligible"] = row["ga"].pk in able
            by_ga[row["ga"].pk] = row
        groups = []
        projected = services.projection(month, by_ga) if month else {}
        budget = services.budgets(month, {pk: row.get("crew") for pk, row in by_ga.items()}) if month else {}
        for pk, row in by_ga.items():
            b = budget.get(pk) or {}
            crew, factor = row.get("crew"), 10
            row["cap"], row["floor"] = b.get("cap"), b.get("floor")
            row["can_convert"] = max(0, (b["cap"] - crew) * factor) if b.get("cap") is not None and crew is not None else None
            row["must_convert"] = max(0, (b["floor"] - crew) * factor) if b.get("floor") is not None and crew is not None else None
        nxt = services.upcoming(month, by_ga) if month else {"planned": [], "waiting": []}
        if month:
            members = list(month.members.select_related("game_account__account"))
            for number in sorted({m.group for m in members}):
                of = [m for m in members if m.group == number]
                pick = lambda role: [by_ga.get(m.game_account_id) or {"ga": m.game_account} for m in of if m.role == role]
                champion = pick(PiracyMember.CHAMPION)
                groups.append({"number": number, "champion": champion[0] if champion else None,
                               "passers": pick(PiracyMember.PASSER), "origins": pick(PiracyMember.ORIGIN),
                               "projection": projected.get(number)})
        routes = (list(month.routes.select_related("raider", "target", "outside").order_by("-created_at")[:20]) if month else [])
        history = [{"month": past, "champions": [c.game_account for c in past.champions.all()]}
                   for past in PiracyMonth.objects.filter(server_id=server).prefetch_related("champions__game_account")[:12]]
        own_ranked = sorted([r for r in data.get("rows", []) if r.get("place")], key=lambda r: int(r["place"]))
        return render(request, "piracy/index.html", {
            **data, "server": server, "servers": services.servers(), "clock_human": _clock_human(data.get("clock")),
            "eligible_count": len(able), "groups": groups, "next_raids": nxt["planned"], "waiting": nxt["waiting"], "routes": routes, "history": history, "own_ranked": own_ranked,
            "targets": PiracyTarget.objects.filter(server_id=server).order_by("player_name") if server else [],
            "marked": services.marked_targets(getattr(request, "organization", None), server) if server else [],
            "can_run": _can_run(request), "base_template": _base(request), "now": timezone.now(), "win_factor_pct": round((services.WIN_FACTOR - 1) * 100, 1),
            "best_odds": _best_odds(data.get("rows", []), able),
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
                    pool = {str(ga.pk): ga for ga in services.eligible(server)}
                    champions = [pool[i] for i in ids if i in pool]
                month = services.plan_month(request.organization, server, champions=champions,
                                            share_percent=int(request.POST.get("share_percent") or 10),
                                            share_every_hours=int(request.POST.get("share_every_hours") or 24),
                                            min_win_percent=int(request.POST.get("min_win_percent") or 95),
                                            min_points=int(request.POST.get("min_points") or 10000))
                names = ", ".join(c.game_account.name for c in month.champions.select_related("game_account"))
                record("piracy.month_planned", target=month, target_label=f"{server}: {names}")
                messages.success(request, f"Campeãs do mês: {names}.")
            elif action == "share":
                month = services.current_month(server)
                if month:
                    month.share_percent = max(0, min(100, int(request.POST.get("share_percent") or 0)))
                    month.share_every_hours = max(1, min(240, int(request.POST.get("share_every_hours") or 24)))
                    month.min_win_percent = max(50, min(100, int(request.POST.get("min_win_percent") or 95)))
                    month.min_points = max(100, int(request.POST.get("min_points") or 10000))
                    month.save(update_fields=["share_percent", "share_every_hours", "min_win_percent", "min_points", "updated_at"])
                    messages.success(request, f"As outras contas convertem {month.share_percent}% a cada {month.share_every_hours}h; "
                                              f"assalto só com {month.min_win_percent}% de chance de vencer ou mais.")
            elif action in ("pause", "resume"):
                month = services.current_month(server)
                if month:
                    month.paused = action == "pause"
                    month.save(update_fields=["paused", "updated_at"])
                    if month.paused:
                        month.routes.filter(status=PiracyRoute.PLANNED).update(status=PiracyRoute.SKIPPED, message="plano pausado")
                    record("piracy.month_paused" if month.paused else "piracy.month_resumed", target=month, target_label=server)
                    messages.success(request, "Plano pausado: as contas jogam como se não houvesse plano." if month.paused
                                     else "Plano retomado.")
            elif action == "close":
                month = services.current_month(server)
                if month:
                    services.close_month(month, "encerrado pelo usuário")
                    record("piracy.month_closed", target=month, target_label=server)
                    messages.success(request, "Plano do mês encerrado.")
            elif action == "add_target":
                name = (request.POST.get("player_name") or "").strip()[:64]
                if not name:
                    raise ValueError("Digite o nome do jogador.")
                own = {str(ga.name or "").strip().casefold() for ga in services.accounts_on(server)}
                if name.casefold() in own:
                    raise ValueError("Esse jogador é uma conta sua: as suas já entram pelo plano do mês.")
                target, _created = PiracyTarget.objects.update_or_create(
                    organization=request.organization, server_id=server, player_name=name,
                    defaults={"active": True, "city_id": (request.POST.get("city_id") or "").strip()[:32],
                              "island_id": (request.POST.get("island_id") or "").strip()[:32]})
                record("piracy.target_added", target=target, target_label=f"{server}: {name}")
                messages.success(request, f"{name} entrou como alvo.")
            elif action == "remove_target":
                target = PiracyTarget.objects.filter(pk=request.POST.get("target")).first()
                if target:
                    record("piracy.target_removed", target=target, target_label=f"{server}: {target.player_name}")
                    target.delete()
                    messages.success(request, "Alvo retirado.")
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect(f"/pirataria/?server={server}")
