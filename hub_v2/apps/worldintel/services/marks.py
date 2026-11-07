"""N-174/N-175: players marked by the organization, and the players that look like someone else's multi account.

A possible multi account is left out of spying, of rich target alerts and of piracy targets; a piracy target picked
by hand comes first in the special piracy cycle. Suggestions only suggest: the owner decides.
"""
from __future__ import annotations

from collections import defaultdict

from django.core.cache import cache

SUGGEST_CACHE_SECONDS = 30 * 60
BIG_PLAYERS = 150            # the largest players of the dump, by total score
SMALL_SHARE = 0.15           # a suspect has at most this share of the big player's score
SHARED_ISLANDS_SHARE = 0.6   # and at least this share of its cities on the big player's islands


def _norm(name) -> str:
    return str(name or "").strip().casefold()


def marks_of(organization, server_id: str, kind: str):
    from apps.worldintel.models import PlayerMark

    if organization is None:
        return PlayerMark.objects.none()
    return PlayerMark._base_manager.filter(organization=organization, server_id=server_id, kind=kind)


def marked(organization, server_id: str, kind: str) -> tuple[set[str], set[str]]:
    """(owner ids, owner names casefolded) marked with that kind."""
    ids, names = set(), set()
    for owner_id, owner_name in marks_of(organization, server_id, kind).values_list("owner_id", "owner_name"):
        if owner_id:
            ids.add(str(owner_id))
        if owner_name:
            names.add(_norm(owner_name))
    return ids, names


def is_marked(owner_id, owner_name, marks: tuple[set[str], set[str]]) -> bool:
    ids, names = marks
    return (bool(owner_id) and str(owner_id) in ids) or (bool(owner_name) and _norm(owner_name) in names)


def toggle(organization, server_id: str, kind: str, *, owner_id: str, owner_name: str, user: str = "",
           main_owner_name: str = "", note: str = "") -> bool:
    """Mark the player, or unmark it if it was marked. Returns whether it is marked now."""
    from apps.worldintel.models import PlayerMark

    existing = marks_of(organization, server_id, kind).filter(owner_name__iexact=owner_name.strip()).first()
    if existing:
        existing.delete()
        cache.delete(_suggest_key(organization, server_id))
        return False
    PlayerMark._base_manager.create(
        organization=organization, server_id=server_id, kind=kind, owner_id=str(owner_id or ""),
        owner_name=owner_name.strip()[:128], main_owner_name=main_owner_name.strip()[:128], note=note.strip()[:255],
        created_by=str(user or "")[:150],
    )
    cache.delete(_suggest_key(organization, server_id))
    return True


def _suggest_key(organization, server_id) -> str:
    return f"player_mark_suggestions:{getattr(organization, 'pk', '-')}:{server_id}"


def suggestions(dump, organization, limit: int = 30) -> list[dict]:
    """Players that look like the support account of a big player: small, with most of their cities on the
    islands where a big player lives (more so in the same alliance). Already marked players are left out."""
    server_id = dump.game_account.server_id if dump.game_account else ""
    key = _suggest_key(organization, server_id) + f":{dump.pk}"
    found = cache.get(key)
    if found is None:
        found = _build_suggestions(dump, organization, server_id)
        cache.set(key, found, SUGGEST_CACHE_SECONDS)
    multi = marked(organization, server_id, "multi")
    return [s for s in found if not is_marked(s["owner_id"], s["owner_name"], multi)][:limit]


def _build_suggestions(dump, organization, server_id) -> list[dict]:
    from apps.game.models import AccountSnapshot
    from apps.worldintel.models import WorldDumpCity, WorldDumpPlayer

    totals, allies = {}, {}
    for owner_id, building, research, army, ally in (WorldDumpPlayer._base_manager.filter(dump=dump).values_list(
            "owner_id", "building_score", "research_score", "army_score", "ally_tag")):
        totals[owner_id] = (building or 0) + (research or 0) + (army or 0)
        allies[owner_id] = ally or ""
    own_city_ids = {str(c.get("id")) for cities in AccountSnapshot._base_manager.filter(
        account__organization=organization).values_list("cities", flat=True) for c in (cities or []) if isinstance(c, dict)}

    islands_of = defaultdict(set)
    cities_of = defaultdict(list)
    names, states = {}, defaultdict(set)
    own_owners = set()
    for owner_id, owner_name, island_id, city_id, state in (WorldDumpCity._base_manager.filter(dump=dump, type="city")
                                                           .values_list("owner_id", "owner_name", "island__island_id",
                                                                        "game_city_id", "state")):
        if not owner_id:
            continue
        islands_of[owner_id].add(island_id)
        cities_of[owner_id].append(island_id)
        names[owner_id] = owner_name
        states[owner_id].add(state or "active")
        if city_id in own_city_ids:
            own_owners.add(owner_id)

    big = sorted((o for o in totals if o in islands_of and o not in own_owners), key=lambda o: -totals[o])[:BIG_PLAYERS]
    big_on_island = defaultdict(set)
    for owner in big:
        for island in islands_of[owner]:
            big_on_island[island].add(owner)

    out = []
    for owner, islands in cities_of.items():
        if owner in own_owners or owner not in totals:
            continue
        best = None
        for main in {m for island in islands for m in big_on_island.get(island, ())}:
            if main == owner or totals[owner] > totals[main] * SMALL_SHARE:
                continue
            shared = sum(1 for island in islands if main in big_on_island.get(island, ()))
            share = shared / len(islands)
            if share < SHARED_ISLANDS_SHARE:
                continue
            same_ally = bool(allies.get(owner)) and allies.get(owner) == allies.get(main)
            score = share + (0.5 if same_ally else 0) + min(shared, 5) * 0.05
            if best is None or score > best["score"]:
                best = {"owner_id": owner, "owner_name": names.get(owner, ""), "ally": allies.get(owner, ""),
                        "main_owner_id": main, "main_owner_name": names.get(main, ""), "main_score": totals[main],
                        "score_total": totals[owner], "shared": shared, "cities": len(islands), "same_ally": same_ally,
                        "states": sorted(states[owner]), "score": round(score, 3)}
        if best:
            out.append(best)
    out.sort(key=lambda s: (-s["score"], -s["main_score"]))
    return out[:200]
