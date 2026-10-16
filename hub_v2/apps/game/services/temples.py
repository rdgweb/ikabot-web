"""Temples and faith (N-141): every island where the organization has a temple, across all its game accounts.

An island's faith is the sum of every city's share (capped at 100%); a city's share is
priests x inhabitants-per-priest / island capacity, so on one island every priest is worth the same share,
whichever account owns it. Reaching 100% without excess therefore means: the priests the island still needs,
spread evenly over our cities there, each within its temple's maximum and its free citizens.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from apps.accounts.models import GameAccount
from apps.game.models import AccountSnapshot

STRENGTH = re.compile(r"^(.*?)\s*\(N[ií]vel\s*(\d+)\s*/\s*(\d+)\)\s*$")


@dataclass
class OwnTemple:
    game_account_id: str
    game_account_name: str
    city_id: str
    city_name: str
    priests: int
    max_priests: int
    free_citizens: int
    share: float
    target: int | None = None

    @property
    def cap(self) -> int:
        return max(self.priests, min(self.max_priests or self.priests, self.priests + max(0, self.free_citizens)))


@dataclass
class Island:
    key: str
    server_id: str
    island_id: str
    island_name: str = ""
    wonder_name: str = ""
    strength: str = ""
    effect: str = ""
    duration: str = ""
    cooldown: str = ""
    available: bool = False
    level: int | None = None
    max_level_reached: bool = False
    upgrading: bool = False
    faith: float | None = None
    total_share: float | None = None
    own: list[OwnTemple] = field(default_factory=list)
    participants: list[dict] = field(default_factory=list)
    share_per_priest: float | None = None
    updated_at: str = ""

    @property
    def own_share(self) -> float:
        return round(sum(t.share for t in self.own), 2)

    @property
    def others_share(self) -> float:
        total = self.total_share if self.total_share is not None else (self.faith or 0.0)
        return max(0.0, round(total - self.own_share, 2))

    @property
    def base_share(self) -> float:
        """Faith that comes from nobody's priests (e.g. Hades' own blessing)."""
        if self.faith is None or self.total_share is None or self.faith >= 100:
            return 0.0
        return max(0.0, round(self.faith - self.total_share, 2))

    @property
    def raw_faith(self) -> float:
        return round(self.base_share + self.others_share + self.own_share, 2)

    @property
    def excess(self) -> float:
        return max(0.0, round(self.raw_faith - 100, 2))

    @property
    def missing(self) -> float:
        return max(0.0, round(100 - self.raw_faith, 2))


def islands_for_organization() -> list[Island]:
    """Every island with one of our temples (the request's organization scope applies)."""
    islands: dict[str, Island] = {}
    accounts = GameAccount.objects.filter(active=True, account__active=True).select_related("account")
    snapshots = {s.game_account_id: s for s in AccountSnapshot.objects.filter(game_account__in=accounts)}
    for ga in accounts:
        snap = snapshots.get(ga.pk)
        if snap is None:
            continue
        wonders = (snap.base_snapshot or {}).get("island_wonders") or {}
        for city in snap.cities if isinstance(snap.cities, list) else []:
            if not isinstance(city, dict):
                continue
            if not any(str(b.get("building") or "") == "temple" for b in city.get("buildings") or [] if isinstance(b, dict)):
                continue
            state = city.get("miracle_state") or {}
            island_id = str(city.get("island_id") or state.get("island_id") or "")
            if not island_id:
                continue
            key = f"{ga.server_id}:{island_id}"
            island = islands.setdefault(key, Island(key=key, server_id=ga.server_id, island_id=island_id))
            wonder = wonders.get(island_id) or {}
            if wonder and str(wonder.get("updated_at") or "") >= island.updated_at:
                island.updated_at = str(wonder.get("updated_at") or "")
                island.level = wonder.get("level")
                island.max_level_reached = bool(wonder.get("max_level_reached"))
                island.upgrading = bool(wonder.get("upgrading"))
                island.faith = wonder.get("faith_percent")
                island.participants = list(wonder.get("participants") or [])
                island.total_share = round(sum(p.get("island_share_percent") or 0 for p in island.participants), 2) if island.participants else None
            if state:
                name = str(state.get("wonder_name") or wonder.get("wonder_name") or "")
                match = STRENGTH.match(name)
                island.wonder_name = match.group(1) if match else name
                island.strength = f"{match.group(2)}/{match.group(3)}" if match else island.strength
                island.island_name = island.island_name or str(state.get("island_name") or "")
                island.effect = str(state.get("wonder_effect") or "")
                island.duration = str(state.get("duration_text") or "")
                island.cooldown = str(state.get("cooldown_text") or "")
                island.available = island.available or str(state.get("activate_state") or "").lower() == "enabled"
                if island.faith is None and state.get("belief_percent") is not None:
                    island.faith = state.get("belief_percent")
                per_priest = int(state.get("citizens_per_priest") or 0)
                capacity = int(state.get("max_citizens_on_island") or 0)
                priests = int(state.get("priests") or 0)
                share = float(state.get("island_conversion_percent") or 0)
                if per_priest and capacity:
                    island.share_per_priest = per_priest * 100.0 / capacity
                elif priests and share and island.share_per_priest is None:
                    island.share_per_priest = share / priests
                island.own.append(OwnTemple(
                    game_account_id=str(ga.pk), game_account_name=ga.name or ga.server_id,
                    city_id=str(city.get("id")), city_name=str(city.get("name") or city.get("id")),
                    priests=priests, max_priests=int(state.get("max_priests") or 0),
                    free_citizens=int(state.get("citizens") or 0), share=share,
                ))
            else:
                island.own.append(OwnTemple(
                    game_account_id=str(ga.pk), game_account_name=ga.name or ga.server_id,
                    city_id=str(city.get("id")), city_name=str(city.get("name") or city.get("id")),
                    priests=0, max_priests=0, free_citizens=0, share=0.0,
                ))
    for island in islands.values():
        island.own.sort(key=lambda t: (t.game_account_name.lower(), t.city_name.lower()))
    return sorted(islands.values(), key=lambda i: (not i.available, i.wonder_name or "~", i.island_id))


def plan_faith(island: Island, target: float = 100.0) -> bool:
    """Fill each own temple's `target`: the priests the island still needs for `target`% faith, spread evenly over
    our cities there (within each temple's maximum and free citizens). False when the island cannot be planned."""
    per = island.share_per_priest
    readable = [t for t in island.own if t.max_priests]
    if not per or not readable:
        return False
    needed_share = max(0.0, target - island.others_share - island.base_share)
    remaining = math.ceil(needed_share / per - 1e-9)
    open_temples = list(readable)
    for t in readable:
        t.target = 0
    while remaining > 0 and open_temples:
        each = math.ceil(remaining / len(open_temples))
        still_open = []
        for t in open_temples:
            give = min(each, t.cap - t.target, remaining)
            t.target += give
            remaining -= give
            if t.target < t.cap:
                still_open.append(t)
        if len(still_open) == len(open_temples) and remaining <= 0:
            break
        open_temples = still_open
    return True


def projected_faith(island: Island) -> float:
    per = island.share_per_priest or 0
    own = sum((t.target if t.target is not None else t.priests) * per for t in island.own)
    return round(min(100.0, island.base_share + island.others_share + own), 1)


def balance_jobs(islands: list[Island], *, target: float = 100.0, created_by=None) -> int:
    """One Ajustar Sacerdotes job per game account with the planned priests of its cities."""
    from apps.jobs.services.workflows import create_job_with_workflow

    by_account: dict[str, dict[str, int]] = {}
    for island in islands:
        if not plan_faith(island, target):
            continue
        for t in island.own:
            if t.target is not None and t.target != t.priests:
                by_account.setdefault(t.game_account_id, {})[t.city_id] = t.target
    created = 0
    for ga in GameAccount.objects.filter(pk__in=list(by_account)).select_related("account__node"):
        create_job_with_workflow(
            account=ga.account, game_account=ga, node=ga.account.node, action_code=35,
            inputs={"target_mode": "city_targets", "city_targets": by_account[str(ga.pk)], "reserve_citizens": 0},
            status="queued", created_by=created_by, trigger_type="manual",
        )
        created += 1
    return created
