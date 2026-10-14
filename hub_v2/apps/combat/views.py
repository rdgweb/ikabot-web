"""Combat reports of the accounts (N-109): the list, and one combat round by round.

The page builds each battlefield in HTML with the game's pieces (services.board), so that it
follows the width of the screen. The JPEG of a round is what goes on Telegram; here it is only
offered as a file to open.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from apps.accounts.models import GameAccount

from .models import CombatReport
from .services.board import GEOMETRY, round_board, tile
from .services.report_parser import RESOURCE_LABELS, parse_rounds, parse_summary, unit_name

PAGE_SIZE = 24
STATES = {"victory": ("Vitória", "win"), "defeat": ("Derrota", "loss")}


def _is_uuid(value) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


def _number(value) -> str:
    return f"{int(value or 0):,}".replace(",", ".")


def _losses(losses) -> list:
    """The units a side lost, the biggest loss first, as tiles for the card."""
    rows = sorted(((int(amount or 0), int(unit)) for unit, amount in (losses or {}).items() if str(unit).isdigit() and int(amount or 0) > 0), reverse=True)
    return [{**tile(unit), "amount": _number(amount)} for amount, unit in rows]


def card(report) -> dict:
    """One combat as the list shows it."""
    if not report.finished:
        label, tone = "Em andamento", "live"
    else:
        label, tone = STATES.get(report.result, ("Encerrado", "done"))
    attacker, defender = _losses(report.attacker_losses), _losses(report.defender_losses)
    return {
        "report": report,
        "label": label,
        "tone": tone,
        "title": (report.battle_title or "").replace("Batalha por", "").strip() or report.target_city_name or f"Combate {report.combat_id}",
        "attacker": report.attacker_name,
        "defender": report.defender_name or report.target_owner,
        "account": getattr(report.game_account, "name", "") or "",
        "when": report.combat_date or report.created_at,
        "attacker_losses": attacker[:4],
        "defender_losses": defender[:4],
        "attacker_lost": _number(sum(int(amount or 0) for amount in (report.attacker_losses or {}).values())),
        "defender_lost": _number(sum(int(amount or 0) for amount in (report.defender_losses or {}).values())),
        "loot": _number(report.total_loot) if report.total_loot else "",
        "naval": report.combat_type == "naval",
    }


class CombatListView(LoginRequiredMixin, TemplateView):
    """GET /combat/ - the combats of the accounts, the newest first."""

    template_name = "combat/list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        reports = CombatReport.objects.select_related("game_account").defer("summary_html", "detailed_html").order_by("-updated_at")
        account = self.request.GET.get("account") or ""
        state = self.request.GET.get("state") or ""
        if _is_uuid(account):
            reports = reports.filter(game_account_id=account)
        else:
            account = ""
        totals = reports.aggregate(
            total=Count("id"), live=Count("id", filter=Q(finished=False)), won=Count("id", filter=Q(finished=True, result="victory")),
            lost=Count("id", filter=Q(finished=True, result="defeat")), loot=Sum("total_loot"),
        )
        if state == "live":
            reports = reports.filter(finished=False)
        elif state in STATES:
            reports = reports.filter(finished=True, result=state)
        page = Paginator(reports, PAGE_SIZE).get_page(self.request.GET.get("page"))
        with_reports = CombatReport.objects.exclude(game_account=None).values_list("game_account_id", flat=True).distinct()
        ctx.update({
            "cards": [card(report) for report in page],
            "page": page,
            "accounts": GameAccount.objects.filter(pk__in=list(with_reports)).order_by("name"),
            "account": account,
            "state": state,
            "live_count": totals["live"] or 0,
            "total": totals["total"] or 0,
            "tiles": [
                {"label": "Combates", "value": _number(totals["total"]), "state": "", "tone": ""},
                {"label": "Em andamento", "value": _number(totals["live"]), "state": "live", "tone": "live"},
                {"label": "Vitórias", "value": _number(totals["won"]), "state": "victory", "tone": "win"},
                {"label": "Derrotas", "value": _number(totals["lost"]), "state": "defeat", "tone": "loss"},
                {"label": "Saque", "value": _number(totals["loot"]), "state": None, "tone": ""},
            ],
            "geometry": GEOMETRY,
            "simulator": _simulator_chip(),
        })
        return ctx


def _simulator_chip() -> dict:
    from .services.checks import scoreboard

    return scoreboard()


class CombatDetailView(LoginRequiredMixin, TemplateView):
    """GET /combat/<pk>/ - one combat: the field of each round, what happened in it, and what it cost."""

    template_name = "combat/detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        report = get_object_or_404(CombatReport.objects.select_related("game_account"), pk=kwargs["pk"])
        own = getattr(report.game_account, "name", "") or ""
        rounds = parse_rounds(report.detailed_html)
        summary = parse_summary(report.summary_html)
        numbers = [item["round"] for item in rounds]
        try:
            current = int(self.request.GET.get("round") or 0)
        except ValueError:
            current = 0
        if current not in numbers:
            current = numbers[-1] if numbers else 0
        units = []
        for item in (summary or {}).get("units") or []:
            units.append({
                **tile(item["unit"]),
                "name": item.get("name") or unit_name(item["unit"]),
                "attacker": item["attacker"] and {"left": _number(item["attacker"]["left"]), "lost": _number(item["attacker"]["lost"]) if item["attacker"]["lost"] else ""},
                "defender": item["defender"] and {"left": _number(item["defender"]["left"]), "lost": _number(item["defender"]["lost"]) if item["defender"]["lost"] else ""},
            })
        ctx.update({
            "report": report,
            "card": card(report),
            "boards": [round_board(item, own) for item in rounds],
            "numbers": numbers,
            "current": current,
            "summary": summary,
            "units": units,
            "loot": [
                {"key": key, "label": RESOURCE_LABELS.get(key, key), "amount": _number(amount), "icon": f"game/resources/icon_{key}.png"}
                for key, amount in ((summary or {}).get("loot") or {}).items()
            ],
            "geometry": GEOMETRY,
            "sim": check_card(report.simulation) if hasattr(report, "simulation") else None,
        })
        return ctx


class CombatSendView(LoginRequiredMixin, View):
    """POST /combat/<pk>/send/ - send one round of a combat to Telegram, with its picture, when asked on the page."""

    def post(self, request, pk):
        import json

        from apps.telegram.services.notifications import notify

        report = get_object_or_404(CombatReport.objects.select_related("game_account__account"), pk=pk)
        numbers = [item["round"] for item in parse_rounds(report.detailed_html)]
        try:
            number = int(request.POST.get("round") or 0)
        except ValueError:
            number = 0
        if number not in numbers:
            number = numbers[-1] if numbers else 0
        sent = False
        if report.game_account_id and numbers:
            # the last round of a combat that is over goes with the outcome, like the notice the hub sends by itself
            final = report.finished and number == numbers[-1]
            sent = notify(
                event_key="combat_report", game_account=report.game_account, account=report.game_account.account,
                title="Combate — enviado pela página", phase="final" if final else "round", combat_id=report.combat_id,
                result=report.result, city_name=report.target_city_name, owner_name=report.target_owner, round=number,
            )
        response = HttpResponse(status=200)
        response["HX-Trigger"] = json.dumps({"toast": {
            "type": "success" if sent else "error",
            "message": f"Round {number} enviado para o Telegram." if sent else "Não foi enviado: o aviso de combate está desligado ou não há chat vinculado.",
        }})
        return response


class CombatRoundImageView(LoginRequiredMixin, View):
    """GET /combat/<pk>/round/<number>.jpg - the picture of that round, the one that goes on Telegram."""

    def get(self, request, pk, number):
        from .services.battle_image import render_battle

        report = get_object_or_404(CombatReport.objects.select_related("game_account"), pk=pk)
        rounds = parse_rounds(report.detailed_html)
        current = next((item for item in rounds if item["round"] == number), None)
        if current is None:
            raise Http404("round")
        last = current is rounds[-1]
        picture = render_battle(
            current,
            parse_summary(report.summary_html) if last and report.finished else None,
            own_name=getattr(report.game_account, "name", "") or "",
        )
        response = HttpResponse(picture, content_type="image/jpeg")
        # a round that is over never changes; the last one may still gain the outcome
        response["Cache-Control"] = "private, max-age=60" if last and not report.finished else "private, max-age=86400"
        return response


# ── N-110: the simulator beside what really happened ──

SIDES = {"attacker": "Atacante", "defender": "Defensor", "draw": "Empate"}


def check_card(check) -> dict:
    """One comparison as the pages show it: who won and what each side lost, real and predicted."""
    real, predicted = check.real_json or {}, check.predicted_json or {}
    guess = (check.inputs_json or {}).get("assumptions") or {}

    def lost(data: dict, side: str) -> int:
        return sum(int(amount or 0) for amount in ((data.get("losses") or {}).get(side) or {}).values())

    report = check.report
    if report is not None:
        title = (report.battle_title or "").replace("Batalha por", "").strip() or report.target_city_name or f"Combate {report.combat_id}"
        url, external = reverse("combat:detail", args=[report.pk]), False
    else:
        title = check.title or check.external_id
        url, external = (f"https://ikalogs.ru/report/{check.external_id}/", True) if check.source == "ikalogs" else ("", False)
    return {
        "check": check,
        "report": report,
        "title": title,
        "url": url,
        "external": external,
        "source": "IkaLogs" if check.source == "ikalogs" else "",
        "winner_known": bool(real.get("winner")),
        "winner_ok": check.winner_ok,
        "real_winner": SIDES.get(real.get("winner"), "?"),
        "predicted_winner": SIDES.get(predicted.get("winner"), "?"),
        "real_rounds": int(real.get("rounds") or 0),
        "predicted_rounds": int(predicted.get("rounds") or 0),
        "sides": [
            {"label": SIDES[side], "tone": tone, "real": _number(lost(real, side)), "predicted": _number(lost(predicted, side)),
             "hit": round(100 * (1 - error))}
            for side, tone, error in (("attacker", "att", check.attacker_error), ("defender", "def", check.defender_error))
        ],
        "wall": f"Muralha nível {guess['wall_level']} · {guess.get('wall_source') or 'padrão'}" if guess.get("wall_level") else "",
    }


class SimulatorView(LoginRequiredMixin, TemplateView):
    """GET /combat/simulador/ - how the simulator did against the real combats, and what it learned from them."""

    template_name = "combat/simulator.html"

    def get_context_data(self, **kwargs):
        from .models import SimulationCheck, SimulatorCalibration
        from .services.checks import by_kind, scoreboard
        from .services.learning import MIN_CHECKS, can_approve

        ctx = super().get_context_data(**kwargs)
        checks = list(SimulationCheck.objects.select_related("report__game_account").defer("report__summary_html", "report__detailed_html")
                      .order_by("source", "-created_at"))
        calibration = SimulatorCalibration.load()
        ctx.update({
            "board": scoreboard(checks),
            "kinds": by_kind(checks),
            "cards": [check_card(check) for check in checks],
            "proposal": calibration.proposed_json or {},
            "approved": calibration.approved_json or {},
            "can_approve": can_approve(calibration.proposed_json or {}),
            "min_checks": MIN_CHECKS,
            "missing": max(0, MIN_CHECKS - len(checks)),
        })
        return ctx


class SimulatorActionView(LoginRequiredMixin, View):
    """POST /combat/simulador/acao/ - recompute the proposal, approve it, or take the approval back."""

    def post(self, request):
        from django.contrib import messages
        from django.shortcuts import redirect

        from .services import learning

        action = request.POST.get("action") or ""
        if action == "propose":
            proposal = learning.propose()
            messages.success(request, f"Proposta recalculada com {proposal['samples']} combate(s).")
        elif action == "approve":
            if learning.approve(request.user.get_username()):
                messages.success(request, "Ajuste aprovado: o simulador passa a usá-lo.")
            else:
                messages.error(request, "Esta proposta ainda não pode ser aprovada.")
        elif action == "revoke":
            learning.revoke()
            messages.success(request, "Ajuste retirado: o simulador voltou ao original.")
        return redirect("combat:simulator")
