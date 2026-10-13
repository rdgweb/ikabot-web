"""Combat reports of the accounts (N-109): the list, one combat round by round, and its picture."""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.views import View
from django.views.generic import TemplateView

from apps.accounts.models import GameAccount

from .models import CombatReport
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


def card(report) -> dict:
    """One combat as the list shows it."""
    if not report.finished:
        label, tone = "Em andamento", "live"
    else:
        label, tone = STATES.get(report.result, ("Encerrado", "done"))
    lost = {side: sum(int(amount or 0) for amount in (losses or {}).values())
            for side, losses in (("attacker", report.attacker_losses), ("defender", report.defender_losses))}
    return {
        "report": report,
        "label": label,
        "tone": tone,
        "title": (report.battle_title or "").replace("Batalha por", "").strip() or report.target_city_name or f"Combate {report.combat_id}",
        "attacker": report.attacker_name,
        "defender": report.defender_name or report.target_owner,
        "account": getattr(report.game_account, "name", "") or "",
        "when": report.combat_date or report.created_at,
        "attacker_lost": _number(lost["attacker"]),
        "defender_lost": _number(lost["defender"]),
        "loot": _number(report.total_loot) if report.total_loot else "",
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
            "live_count": CombatReport.objects.filter(finished=False).count(),
            "total": CombatReport.objects.count(),
        })
        return ctx


class CombatDetailView(LoginRequiredMixin, TemplateView):
    """GET /combat/<pk>/ - one combat: the field of each round, and what it cost."""

    template_name = "combat/detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        report = get_object_or_404(CombatReport.objects.select_related("game_account"), pk=kwargs["pk"])
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
                "name": item.get("name") or unit_name(item["unit"]),
                "attacker": item["attacker"] and {"left": _number(item["attacker"]["left"]), "lost": _number(item["attacker"]["lost"]) if item["attacker"]["lost"] else ""},
                "defender": item["defender"] and {"left": _number(item["defender"]["left"]), "lost": _number(item["defender"]["lost"]) if item["defender"]["lost"] else ""},
            })
        ctx.update({
            "report": report,
            "card": card(report),
            "numbers": numbers,
            "current": current,
            "summary": summary,
            "units": units,
            "loot": [(RESOURCE_LABELS.get(key, key), _number(amount)) for key, amount in ((summary or {}).get("loot") or {}).items()],
        })
        return ctx


class CombatRoundImageView(LoginRequiredMixin, View):
    """GET /combat/<pk>/round/<number>.jpg - the battlefield of that round, as the game shows it."""

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
