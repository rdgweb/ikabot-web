"""View: DemolishBuildingsView -- demole edificios de uma conta via jobs (N-57).

POST game_account_id + items (JSON) + confirm -> um job ac=1303 por cidade.
Cada item: {city_id, position, building, level, mode: "levels"|"complete", levels}.

Demolir nao tem volta: o servidor confere cada item contra o snapshot (mesmo
edificio, mesmo nivel, nao em obra, nunca a Prefeitura) e exige a palavra de
confirmacao digitada. dry_run=1 cria jobs de simulacao, que so abrem a tela de
confirmacao do jogo e nao demolem nada (nao exigem a palavra).
O agent repete as mesmas travas contra o jogo antes de cada passo.
"""

import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views import View

from apps.accounts.models import GameAccount
from apps.game.models import AccountSnapshot
from apps.jobs.services.workflows import create_job_with_workflow
from core.catalogs import get_building_info

CONFIRM_WORD = "DEMOLIR"
MAX_ITEMS = 60
NOT_DEMOLISHABLE = {"townHall"}


def _int(value, default=-1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _building_label(building: str) -> str:
    return (get_building_info(building) or {}).get("name") or building


def validate_items(raw: str, cities: list) -> tuple[dict[str, list[dict]], str]:
    """Items grouped by city id, or ({}, error). Checked against the snapshot cities."""
    try:
        data = json.loads(raw or "")
    except (TypeError, ValueError):
        return {}, "Lista de edificios invalida."
    if not isinstance(data, list) or not data:
        return {}, "Nenhum edificio escolhido."
    if len(data) > MAX_ITEMS:
        return {}, f"No maximo {MAX_ITEMS} edificios por vez."

    by_city = {str(c.get("id") or ""): c for c in cities if isinstance(c, dict)}
    grouped: dict[str, list[dict]] = {}
    seen: set[tuple[str, int]] = set()
    for entry in data:
        if not isinstance(entry, dict):
            return {}, "Lista de edificios invalida."
        city_id = str(entry.get("city_id") or "").strip()
        position = _int(entry.get("position"))
        building = str(entry.get("building") or "").strip()
        level = _int(entry.get("level"))
        complete = entry.get("mode") == "complete"
        levels = level if complete else _int(entry.get("levels"))

        city = by_city.get(city_id)
        if city is None:
            return {}, "Cidade nao pertence a esta conta."
        city_name = str(city.get("name") or city_id)
        current = next(
            (b for b in city.get("buildings") or [] if isinstance(b, dict) and _int(b.get("position")) == position),
            None,
        )
        label = f"{_building_label(building)} em {city_name}"
        if (city_id, position) in seen:
            return {}, f"{label}: posicao repetida na lista."
        if current is None or str(current.get("building") or "") != building or building in ("", "empty"):
            return {}, f"{label}: o edificio nessa posicao mudou. Recarregue a pagina."
        if building in NOT_DEMOLISHABLE:
            return {}, f"{label}: esse edificio nao pode ser demolido."
        if _int(current.get("level")) != level or level < 1:
            return {}, f"{label}: o nivel mudou (agora {_int(current.get('level'), 0)}). Recarregue a pagina."
        if current.get("is_upgrading"):
            return {}, f"{label}: edificio em obra."
        if not (1 <= levels <= level):
            return {}, f"{label}: quantidade de niveis invalida."

        seen.add((city_id, position))
        grouped.setdefault(city_id, []).append({
            "position": position,
            "building": building,
            "level": level,
            "levels": levels,
            "complete": complete,
            "name": _building_label(building),
        })
    return grouped, ""


class DemolishBuildingsView(LoginRequiredMixin, View):
    def post(self, request):
        ga_id = request.POST.get("game_account_id")
        dry_run = request.POST.get("dry_run") == "1"
        if not ga_id:
            return JsonResponse({"ok": False, "error": "Dados incompletos."}, status=400)
        if not dry_run and (request.POST.get("confirm") or "").strip() != CONFIRM_WORD:
            return JsonResponse({"ok": False, "error": f"Digite {CONFIRM_WORD} para confirmar."}, status=400)

        try:
            ga = GameAccount.objects.select_related("account__node").get(pk=ga_id)
        except (GameAccount.DoesNotExist, ValueError):
            return JsonResponse({"ok": False, "error": "Conta nao encontrada."}, status=404)
        if not ga.account.node:
            return JsonResponse({"ok": False, "error": "Conta sem no atribuido."}, status=400)

        snapshot = AccountSnapshot.objects.filter(game_account=ga).first()
        cities = snapshot.cities if snapshot and isinstance(snapshot.cities, list) else []
        grouped, error = validate_items(request.POST.get("items"), cities)
        if error:
            return JsonResponse({"ok": False, "error": error}, status=400)

        names = {str(c.get("id") or ""): str(c.get("name") or c.get("id")) for c in cities if isinstance(c, dict)}
        for city_id, items in grouped.items():
            create_job_with_workflow(
                account=ga.account,
                game_account=ga,
                node=ga.account.node,
                action_code=1303,
                inputs={"city_id": city_id, "city_name": names.get(city_id, city_id), "items": items, "dry_run": dry_run},
                status="queued",
            )

        total = sum(len(items) for items in grouped.values())
        what = "Simulacao enviada" if dry_run else "Demolicao enviada"
        return JsonResponse({
            "ok": True,
            "message": f"{what}: {total} edificio(s) em {len(grouped)} cidade(s). Acompanhe nos jobs.",
        })
