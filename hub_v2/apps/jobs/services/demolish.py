"""Demolir Edificios (ac=1303, N-57): dados do formulario e validacao do envio.

A acao segue o fluxo normal de Acoes do Jogo (catalogo -> conta -> formulario).
Demolir nao tem volta, entao o servidor confere cada item contra o snapshot
(mesmo edificio, mesmo nivel, nao em obra, nunca a Prefeitura) e exige as
confirmacoes do formulario. O agent repete as travas contra o jogo antes de
cada passo. A simulacao (dry_run) nao exige confirmacoes: nao demole nada.
"""

from __future__ import annotations

import json

from django.templatetags.static import static

from core.catalogs import get_building_info

CONFIRM_WORD = "DEMOLIR"
MAX_ITEMS = 60
NOT_DEMOLISHABLE = {"townHall"}


def _int(value, default=-1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def building_label(building: str) -> str:
    return str((get_building_info(building) or {}).get("name") or building)


def _building_icon(building: str) -> str:
    icon = (get_building_info(building) or {}).get("icon")
    return static(f"game/buildings/{icon}") if icon else static("game/buildings/townhall.png")


def demolish_form_context(cities: list) -> dict:
    """Cities with their buildings exactly as the game names them (raw snapshot)."""
    out = []
    for city in cities or []:
        if not isinstance(city, dict):
            continue
        buildings = []
        for item in city.get("buildings") or []:
            if not isinstance(item, dict):
                continue
            building = str(item.get("building") or "").strip()
            level = _int(item.get("level"), 0)
            if not building or building == "empty":
                continue
            blocked = ""
            if building in NOT_DEMOLISHABLE:
                blocked = "A Prefeitura nao pode ser demolida"
            elif item.get("is_upgrading"):
                blocked = "Em obra"
            elif level < 1:
                blocked = "Sem nivel para tirar"
            buildings.append({
                "position": _int(item.get("position"), 0),
                "building": building,
                "name": building_label(building),
                "icon": _building_icon(building),
                "level": level,
                "blocked": blocked,
            })
        buildings.sort(key=lambda b: b["position"])
        out.append({
            "id": str(city.get("id") or ""),
            "name": str(city.get("name") or city.get("id") or "Cidade"),
            "buildings": buildings,
        })
    return {"demolish_ui": {"cities": out, "confirm_word": CONFIRM_WORD}}


def validate_items(raw: str, cities: list) -> tuple[dict[str, list[dict]], str]:
    """Items grouped by city id, or ({}, error). Checked against the snapshot cities."""
    try:
        data = json.loads(raw or "")
    except (TypeError, ValueError):
        return {}, "Lista de edificios invalida."
    if not isinstance(data, list) or not data:
        return {}, "Escolha ao menos um edificio para demolir."
    if len(data) > MAX_ITEMS:
        return {}, f"No maximo {MAX_ITEMS} edificios por vez."

    by_city = {str(c.get("id") or ""): c for c in cities or [] if isinstance(c, dict)}
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
        # the game has no captcha window for a level-1 building: its last level removes it
        complete = complete and level > 1

        city = by_city.get(city_id)
        if city is None:
            return {}, "Cidade nao pertence a esta conta."
        city_name = str(city.get("name") or city_id)
        current = next(
            (b for b in city.get("buildings") or [] if isinstance(b, dict) and _int(b.get("position")) == position),
            None,
        )
        label = f"{building_label(building)} em {city_name}"
        if (city_id, position) in seen:
            return {}, f"{label}: posicao repetida na lista."
        if current is None or str(current.get("building") or "") != building or building in ("", "empty"):
            return {}, f"{label}: o edificio nessa posicao mudou. Abra o formulario de novo."
        if building in NOT_DEMOLISHABLE:
            return {}, f"{label}: esse edificio nao pode ser demolido."
        if _int(current.get("level")) != level or level < 1:
            return {}, f"{label}: o nivel mudou (agora {_int(current.get('level'), 0)}). Abra o formulario de novo."
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
            "name": building_label(building),
        })
    return grouped, ""


def confirmation_error(post) -> str:
    """What is still missing from the form's confirmations ("" = all given)."""
    if str(post.get("demolish_acknowledge") or "").strip().lower() not in ("on", "1", "true"):
        return "Marque que entende que a demolicao nao tem volta."
    # the field shows upper case whatever is typed, so compare the same way
    if str(post.get("demolish_confirm_word") or "").strip().upper() != CONFIRM_WORD:
        return f"Digite {CONFIRM_WORD} para confirmar."
    return ""
