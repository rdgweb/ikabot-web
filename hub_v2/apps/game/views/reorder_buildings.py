"""View: ReorderBuildingsView -- reorganiza edificios de uma cidade via job (N-50).

POST game_account_id + city_id + swaps (JSON [[de, para], ...]) -> cria job
ac=1302. O agent relê as posicoes no jogo, valida cada troca com as regras do
proprio jogo, salva e atualiza o snapshot.
"""

import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views import View

from apps.accounts.models import GameAccount
from apps.game.models import AccountSnapshot
from apps.jobs.services.workflows import create_job_with_workflow

MAX_POSITION = 24
MAX_SWAPS = 100


def parse_swaps(raw: str) -> list[list[int]] | None:
    """[[from, to], ...] with valid, distinct positions; None if malformed."""
    try:
        data = json.loads(raw or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(data, list) or not data or len(data) > MAX_SWAPS:
        return None
    swaps: list[list[int]] = []
    for pair in data:
        if not isinstance(pair, list) or len(pair) != 2:
            return None
        try:
            frm, to = int(pair[0]), int(pair[1])
        except (TypeError, ValueError):
            return None
        if frm == to or not (0 <= frm <= MAX_POSITION) or not (0 <= to <= MAX_POSITION):
            return None
        swaps.append([frm, to])
    return swaps


class ReorderBuildingsView(LoginRequiredMixin, View):
    def post(self, request):
        ga_id = request.POST.get("game_account_id")
        city_id = str(request.POST.get("city_id") or "").strip()
        swaps = parse_swaps(request.POST.get("swaps"))

        if not ga_id or not city_id:
            return JsonResponse({"ok": False, "error": "Dados incompletos."}, status=400)
        if swaps is None:
            return JsonResponse({"ok": False, "error": "Lista de trocas invalida."}, status=400)

        try:
            ga = GameAccount.objects.select_related("account__node").get(pk=ga_id)
        except (GameAccount.DoesNotExist, ValueError):
            return JsonResponse({"ok": False, "error": "Conta nao encontrada."}, status=404)
        if not ga.account.node:
            return JsonResponse({"ok": False, "error": "Conta sem no atribuido."}, status=400)

        snapshot = AccountSnapshot.objects.filter(game_account=ga).first()
        cities = snapshot.cities if snapshot and isinstance(snapshot.cities, list) else []
        city = next((c for c in cities if isinstance(c, dict) and str(c.get("id") or "") == city_id), None)
        if city is None:
            return JsonResponse({"ok": False, "error": "Cidade nao pertence a esta conta."}, status=400)

        create_job_with_workflow(
            account=ga.account,
            game_account=ga,
            node=ga.account.node,
            action_code=1302,
            inputs={"city_id": city_id, "city_name": str(city.get("name") or city_id), "swaps": swaps},
            status="queued",
        )
        return JsonResponse({
            "ok": True,
            "message": f"Reorganizacao de {city.get('name') or city_id} enviada ({len(swaps)} troca(s)).",
        })
