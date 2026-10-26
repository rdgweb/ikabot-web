"""Temples and faith (N-141): every island where the organization has a temple, across all its game accounts.

An island's faith is the sum of every city's share (capped at 100%); a city's share is
priests x inhabitants-per-priest / island capacity, so on one island every priest is worth the same share,
whichever account owns it. Reaching 100% without excess therefore means: the priests the island still needs,
spread evenly over our cities there, each within its temple's maximum and its free citizens.
Free citizens are not always enough: the plan says how far each island gets and what holds each temple back
(the temple is full, or the city has no free citizens left after the reserve it keeps).
"""
from __future__ import annotations

import json
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
    reserve: int = 0
    # N-160: what the temple reading of this city said about the account's miracle (seconds left, and when read)
    active_left: int = 0
    regen_left: int = 0
    state_read_at: str = ""

    @property
    def cap(self) -> int:
        return max(self.priests, min(self.max_priests or self.priests, self.priests + max(0, self.free_citizens - self.reserve)))

    @property
    def limit(self) -> str:
        """What stops this temple from taking more priests in the plan: "templo" (full), "populacao" (no free
        citizens left) or "" (it got all the plan asked of it)."""
        if self.target is None or self.target < self.cap:
            return ""
        if self.max_priests and self.target >= self.max_priests:
            return "templo"
        return "populacao"

    @property
    def citizens_used(self) -> int:
        return max(0, (self.target or 0) - self.priests) if self.target is not None else 0


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
    timers: list[dict] = field(default_factory=list)   # N-148: our accounts' miracle in effect or regenerating
    short_priests: int = 0          # priests the plan could not place (no room in the temples or no free citizens)

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


def _free_citizens(city: dict, state: dict) -> int:
    """The free citizens of the city: Verificar Status reads them more often than the temple."""
    value = city.get("free_citizens")
    if value is None:
        value = state.get("citizens")
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _plain_text(value) -> str:
    """The game sends some texts as {"text": "..."}; older readings kept that as "{'text': '...'}"."""
    if isinstance(value, dict):
        return str(value.get("text") or "")
    text = str(value or "")
    match = re.match(r"^\{'text':\s*'(.*)'\}(.*)$", text, re.S)
    return (match.group(1) + match.group(2)).strip() if match else text


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
                island.effect = _plain_text(state.get("wonder_effect"))
                # N-160: only the miracle's own duration/regeneration ("Duração: 4h", "Regeneração: 1D 12h");
                # while it is in effect or regenerating the game shows the time left instead
                duration_text = str(state.get("duration_text") or "")
                cooldown_text = str(state.get("cooldown_text") or "")
                if duration_text.startswith("Duração"):
                    island.duration = duration_text
                if cooldown_text.startswith("Regeneração"):
                    island.cooldown = cooldown_text
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
                    free_citizens=_free_citizens(city, state), share=share,
                    active_left=_span_seconds(duration_text) if ACTIVE_LEFT.search(duration_text) else 0,
                    regen_left=_span_seconds(cooldown_text) if REGEN_LEFT.search(cooldown_text) else 0,
                    state_read_at=str(state.get("updated_at") or ""),
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


# N-165: the island keeps growing (with the same priests, Hell went from 9.09% to 8.27% of the island in a day and a
# half), so aiming at exactly 100% leaves it under 100% right after. "100%" is aimed with a margin.
FAITH_MARGIN = 3.0
FAITH_TARGET = 100.0 + FAITH_MARGIN


def faith_goal(target) -> float:
    """The conversion to aim at for a faith target: 100% (or more) gets the margin; a lower target is kept."""
    try:
        target = float(target)
    except (TypeError, ValueError):
        target = 100.0
    return FAITH_TARGET if target >= 100.0 else target


def plan_faith(island: Island, target: float = FAITH_TARGET, reserve: int = 0) -> bool:
    """Fill each own temple's `target`: the priests the island still needs for `target`% faith, spread evenly over
    our cities there (within each temple's maximum and the free citizens above `reserve`). What does not fit is
    left in `island.short_priests`. False when the island cannot be planned."""
    per = island.share_per_priest
    readable = [t for t in island.own if t.max_priests]
    island.short_priests = 0
    if not per or not readable:
        return False
    for t in readable:
        t.reserve = max(0, int(reserve or 0))
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
    island.short_priests = max(0, remaining)
    return True


def projected_faith(island: Island) -> float:
    per = island.share_per_priest or 0
    own = sum((t.target if t.target is not None else t.priests) * per for t in island.own)
    return round(min(FAITH_TARGET, island.base_share + island.others_share + own), 1)


def balance_jobs(islands: list[Island], *, target: float = FAITH_TARGET, reserve: int = 0, created_by=None) -> int:
    """One Ajustar Sacerdotes job per game account with the planned priests of its cities."""
    target = faith_goal(target)
    from apps.jobs.services.workflows import create_job_with_workflow

    by_account: dict[str, dict[str, int]] = {}
    for island in islands:
        if not plan_faith(island, target, reserve):
            continue
        for t in island.own:
            if t.target is not None and t.target != t.priests:
                by_account.setdefault(t.game_account_id, {})[t.city_id] = t.target
    created = 0
    for ga in GameAccount.objects.filter(pk__in=list(by_account)).select_related("account__node"):
        create_job_with_workflow(
            account=ga.account, game_account=ga, node=ga.account.node, action_code=35,
            inputs={"target_mode": "city_targets", "city_targets": by_account[str(ga.pk)], "reserve_citizens": max(0, int(reserve or 0))},
            status="queued", created_by=created_by, trigger_type="manual",
        )
        created += 1
    return created


ACTIVE_LEFT = re.compile(r"Ativado por", re.IGNORECASE)
REGEN_LEFT = re.compile(r"Tempo restante", re.IGNORECASE)


def _span_seconds(text: str) -> int:
    """ "Duração: 1D" / "Regeneração: 3D 12h" / "de imediato" -> seconds."""
    total = 0
    for amount, unit in re.findall(r"(\d+)\s*([DdHhMmSs])\b", str(text or "")):
        total += int(amount) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit.lower()]
    return total


def attach_miracle_timers(islands: list[Island], *, days: int = 8) -> None:
    """N-148/N-160: for each of our accounts, the island's miracle in effect or regenerating: from the Ativar Milagre
    job that activated it (and the miracle's own duration/regeneration) or, when newer, from the temple reading that
    showed the time left. Each timer says why it was activated (shipment, attack, Telegram, by hand)."""
    from datetime import timedelta

    from django.utils import timezone
    from django.utils.dateparse import parse_datetime

    from apps.jobs.models import Job, JobLog

    by_city = {(t.game_account_id, t.city_id): (island, t) for island in islands for t in island.own}
    if not by_city:
        return
    now = timezone.now()
    since = now - timedelta(days=days)
    jobs = Job.objects.filter(action_code=11, status="finished", finished_at__gte=since,
                              game_account_id__in={ga for ga, _ in by_city}).only("pk", "game_account_id", "inputs_json")
    activated = {
        log.job_id: log.created_at
        for log in JobLog.objects.filter(job__in=jobs, message__startswith="Milagre ativado").only("job_id", "created_at")
    }
    timers: dict[tuple[str, str], dict] = {}

    def offer(island, account_id, account_name, *, active_until, available_at, reason, seen_at):
        key = (island.key, account_id)
        current = timers.get(key)
        if current is None or seen_at > current["seen_at"]:
            timers[key] = {"island": island, "account": account_name, "active_until": active_until,
                           "available_at": available_at, "reason": reason or (current or {}).get("reason", ""),
                           "seen_at": seen_at}
        elif reason and not current["reason"]:
            current["reason"] = reason

    for job in jobs:
        when = activated.get(job.pk)
        if when is None:
            continue
        try:
            inputs = json.loads(job.inputs_json or "{}")
        except ValueError:
            continue
        hit = by_city.get((str(job.game_account_id), str(inputs.get("city") or "")))
        if hit is None:
            continue
        island, temple = hit
        duration, cooldown = _span_seconds(island.duration), _span_seconds(island.cooldown)
        offer(island, temple.game_account_id, temple.game_account_name,
              active_until=when + timedelta(seconds=duration) if duration else None,
              available_at=when + timedelta(seconds=cooldown) if cooldown else None,
              reason=_reason_label(inputs.get("reason")), seen_at=when)

    for island in islands:
        for temple in island.own:
            if not (temple.active_left or temple.regen_left):
                continue
            read_at = parse_datetime(temple.state_read_at) if temple.state_read_at else None
            if read_at is None:
                continue
            if timezone.is_naive(read_at):
                read_at = timezone.make_aware(read_at)
            active_until = read_at + timedelta(seconds=temple.active_left) if temple.active_left else None
            cooldown = _span_seconds(island.cooldown)
            available_at = (read_at + timedelta(seconds=temple.regen_left) if temple.regen_left
                            else (active_until - timedelta(seconds=_span_seconds(island.duration)) + timedelta(seconds=cooldown)
                                  if active_until and cooldown else None))
            offer(island, temple.game_account_id, temple.game_account_name,
                  active_until=active_until, available_at=available_at, reason="", seen_at=read_at)

    for timer in sorted(timers.values(), key=lambda t: t["account"].lower()):
        active_until, available_at = timer["active_until"], timer["available_at"]
        if active_until and active_until <= now:
            active_until = None
        if active_until is None and (available_at is None or available_at <= now):
            continue
        timer["island"].timers.append({"account": timer["account"], "active_until": active_until,
                                       "available_at": available_at if available_at and available_at > now else None,
                                       "reason": timer["reason"]})


def _reason_label(reason) -> str:
    """Why the miracle was asked for, in a few words (the reason the hub or the agent wrote in the job)."""
    text = str(reason or "").lower()
    if not text:
        return ""
    if "envio" in text or "navio" in text:
        return "envio grande"
    if "ataque" in text:
        return "ataque chegando"
    if "combate" in text or "perdendo" in text:
        return "perdendo o combate"
    if "telegram" in text:
        return "Telegram"
    return str(reason)[:40]

# N-165: keep the islands we already worship at 100% faith. The temple reading (ac=35 "keep", spawned by Verificar
# Status) only reads; when the island's population grows each priest converts a smaller share and the faith slides
# below 100% with nobody adjusting. With the switch on, the reading of an account is followed by its adjustment.
FAITH_FLOOR = 100.0                      # island conversion below this asks for more priests (aimed at FAITH_TARGET)


def faith_settings(organization) -> dict:
    from apps.orgs.services import preferences_of

    prefs = (preferences_of(organization).miracle_rules or {}) if organization is not None else {}
    try:
        reserve = max(0, int(prefs.get("faith_reserve") or 0))
    except (TypeError, ValueError):
        reserve = 0
    return {"auto_faith": bool(prefs.get("auto_faith")), "faith_reserve": reserve}


def save_faith_settings(organization, *, auto: bool, reserve: int) -> None:
    from apps.orgs.services import preferences_of

    prefs = preferences_of(organization)
    prefs.miracle_rules = {**(prefs.miracle_rules or {}), "auto_faith": bool(auto), "faith_reserve": max(0, int(reserve or 0))}
    prefs.save(update_fields=["miracle_rules", "updated_at"])


def auto_balance_after_reading(game_account) -> int:
    """After an account's temple reading: when its organization keeps the faith, the islands where this account
    already has priests and whose conversion is under 100% get this account's priests adjusted. Islands with no
    priests of ours are left alone (the Equilibrar button is for them). Returns the jobs created (0 or 1)."""
    from apps.jobs.models import Job
    from apps.jobs.services.workflows import create_job_with_workflow
    from apps.orgs.scope import activate, deactivate

    organization = getattr(game_account.account, "organization", None)
    settings_ = faith_settings(organization)
    if organization is None or not settings_["auto_faith"]:
        return 0
    pending = Job.objects.filter(game_account=game_account, action_code=35, status__in=("queued", "scheduled", "running"),
                                 inputs_json__contains="city_targets").exists()
    if pending:
        return 0
    token = activate(organization)
    try:
        islands = islands_for_organization()
    finally:
        deactivate(token)
    ga_id = str(game_account.pk)
    targets: dict[str, int] = {}
    for island in islands:
        if island.own_share <= 0 or island.total_share is None or island.total_share >= FAITH_FLOOR:
            continue
        if not plan_faith(island, FAITH_TARGET, settings_["faith_reserve"]):
            continue
        for t in island.own:
            if t.game_account_id == ga_id and t.target is not None and t.target > t.priests:
                targets[t.city_id] = t.target
    if not targets:
        return 0
    create_job_with_workflow(
        account=game_account.account, game_account=game_account, node=game_account.account.node, action_code=35,
        inputs={"target_mode": "city_targets", "city_targets": targets, "reserve_citizens": settings_["faith_reserve"],
                "reason": "fé abaixo de 100% (automático)"},
        status="queued", trigger_type="automation",
    )
    return 1
