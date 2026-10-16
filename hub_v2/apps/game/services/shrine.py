"""Santuário dos Deuses (N-143): what each city worships, whether it blesses anything, and the god it should worship.

Read from the game (s78, Santuário 27): each god blesses one resource and only a city producing it gains;
every city worshipping a god gets the same bonus (the shrine's "Bênção máxima"); a god out of grace blesses 0%.
So the god of a city is the god of its luxury good; Pã (wood) and Pluto (gold) bless any city.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apps.accounts.models import GameAccount
from apps.game.models import AccountSnapshot

GODS = {
    1: {"name": "Pã", "image": "game/gods/pan.png", "good": "madeira", "tradegood": None},
    2: {"name": "Dionísio", "image": "game/gods/dionysus.png", "good": "vinho", "tradegood": 1},
    3: {"name": "Tique", "image": "game/gods/tyche.png", "good": "mármore", "tradegood": 2},
    4: {"name": "Pluto", "image": "game/gods/plutus.png", "good": "ouro", "tradegood": None},
    5: {"name": "Teia", "image": "game/gods/theia.png", "good": "cristal", "tradegood": 3},
    6: {"name": "Hefesto", "image": "game/gods/hephaestus.png", "good": "enxofre", "tradegood": 4},
}
GOD_BY_TRADEGOOD = {meta["tradegood"]: god_id for god_id, meta in GODS.items() if meta["tradegood"]}


@dataclass
class CityWorship:
    city_id: str
    name: str
    tradegood: int
    god_id: int | None
    bonus: str = ""
    generated: str = ""
    status: str = ""          # ok | no_god | wrong_good | no_grace | unknown
    recommended: int | None = None

    @property
    def god(self):
        return GODS.get(self.god_id or 0)

    @property
    def recommended_god(self):
        return GODS.get(self.recommended or 0)


@dataclass
class Shrine:
    game_account_id: str
    game_account_name: str
    city_name: str
    level: int
    favor: int
    bonus: str
    gods: list[dict] = field(default_factory=list)
    cities: list[CityWorship] = field(default_factory=list)
    mapped: bool = True        # the reading tells which city each slot belongs to (agent 0.1.120+)
    updated_at: str = ""

    @property
    def problems(self) -> int:
        return sum(1 for c in self.cities if c.status in ("wrong_good", "no_grace", "no_god"))


def _grace_seconds(god: dict) -> int:
    if "grace_seconds" in god:
        return int(god.get("grace_seconds") or 0)
    text = str(god.get("grace_period") or "-").strip()
    return 0 if text in ("", "-") else 1


def shrines_for_organization() -> list[Shrine]:
    """One card per game account with a shrine reading (the request's organization scope applies)."""
    shrines = []
    accounts = GameAccount.objects.filter(active=True, account__active=True)
    for snap in AccountSnapshot.objects.filter(game_account__in=accounts).select_related("game_account"):
        cities = snap.cities if isinstance(snap.cities, list) else []
        state = next((c.get("shrine_state") for c in cities if isinstance(c, dict) and c.get("shrine_state")), None)
        if not state:
            continue
        gods_state = {int(k): v for k, v in (state.get("gods") or {}).items() if str(k).isdigit()}
        slots = [w for w in state.get("worship_assignments") or [] if isinstance(w, dict)]
        by_city = {str(w.get("city_id")): w for w in slots if w.get("city_id")}
        css_to_id = {f"god_{css}": gid for gid, css in ((1, "pan"), (2, "dionysus"), (3, "tyche"), (4, "plutus"),
                                                      (5, "theia"), (6, "hephaestos"))}
        bonus = next((str(w.get("current_bonus")) for w in slots if str(w.get("current_bonus") or "0") not in ("0", "0,00")), "")
        worshipped = {int(g) for g in state.get("gods_worshipped") or []} or {
            css_to_id.get(str(w.get("god_css") or "")) for w in slots} - {None}
        shrine = Shrine(
            game_account_id=str(snap.game_account_id), game_account_name=snap.game_account.name or "",
            city_name=str(state.get("city_name") or ""), level=int(state.get("building_level") or 0),
            favor=int(state.get("current_favor") or 0), bonus=bonus, mapped=bool(by_city),
            updated_at=str(state.get("updated_at") or ""),
        )
        for god_id in sorted(worshipped):
            g = gods_state.get(god_id) or {}
            shrine.gods.append({**GODS[god_id], "id": god_id, "grace": str(g.get("grace_period") or "-"),
                                "in_grace": _grace_seconds(g) > 0})
        for city in cities:
            if not isinstance(city, dict) or not city.get("id"):
                continue
            tradegood = int(city.get("produced_tradegood") or city.get("tradegood") or 0)
            slot = by_city.get(str(city.get("id")))
            god_id = slot.get("god_id") or css_to_id.get(str(slot.get("god_css") or "")) if slot else None
            row = CityWorship(city_id=str(city.get("id")), name=str(city.get("name") or ""), tradegood=tradegood,
                              god_id=god_id, bonus=str((slot or {}).get("current_bonus") or ""),
                              generated=str((slot or {}).get("generated_resources") or ""),
                              recommended=GOD_BY_TRADEGOOD.get(tradegood, 1))
            if not by_city:
                row.status = "unknown"
            elif not god_id:
                row.status = "no_god"
            elif GODS[god_id]["tradegood"] and GODS[god_id]["tradegood"] != tradegood:
                row.status = "wrong_good"
            elif _grace_seconds(gods_state.get(god_id) or {}) <= 0:
                row.status = "no_grace"
            else:
                row.status = "ok"
            shrine.cities.append(row)
        shrines.append(shrine)
    return sorted(shrines, key=lambda s: (-s.problems, s.game_account_name.lower()))
