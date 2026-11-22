"""Piracy page and the month's champions (N-210/N-211).

Read side: what the hub already receives — the ranking the Pirate Fortress job reads (snapshot.piracy_highscore), the
job's own counters (capture points, crew, missions), the pirate raids on their way (attack_alert_state) and the
piracy captchas.

Plan side: two champions a month from different lobbies (a lottery among the accounts that were not champions yet);
each group has passers (lobbies other than its champion's) and origins. Whenever a ship is free the hub decides the
raid on the spot (see next_for). Fixed rules: the same lobby never raids itself; the same raider never raids the same
account twice in 7 days; only sure wins (crew > target crew x 1.1 / 0.9) among the organization's accounts.
"""
from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from .models import PiracyChampion, PiracyMember, PiracyMonth, PiracyRoute, PiracyTarget

PIRATE_ACTION = 17
CHAMPIONS = 2
PASSERS_PER_GROUP = 3
MONTH_FALLBACK_DAYS = 30


# ── reading ────────────────────────────────────────────────────────────────────────────────────────────────────────

def _fortress_city(cities) -> dict | None:
    for city in cities or []:
        if not isinstance(city, dict):
            continue
        for building in city.get("buildings") or []:
            if isinstance(building, dict) and str(building.get("building") or "") == "pirateFortress":
                return {"id": str(city.get("id") or ""), "island_id": str(city.get("island_id") or ""),
                        "name": city.get("name") or "", "level": int(building.get("level") or 0),
                        "x": city.get("x"), "y": city.get("y")}
    return None


def town_room(cities) -> int | None:
    """Free town slots (the palace of level N holds N + 1 towns); None when the snapshot does not say. A raid of the
    plan founds a temporary colony next to the target, so whoever raids needs one."""
    cities = [c for c in cities or [] if isinstance(c, dict)]
    palace = max([int(b.get("level") or 0) for c in cities for b in (c.get("buildings") or [])
                  if isinstance(b, dict) and str(b.get("building") or "") == "palace"] or [0])
    if not cities or not palace:
        return None
    return max(0, palace + 1 - len(cities))


def rooms(accounts) -> dict:
    from apps.game.models import AccountSnapshot

    return {s.game_account_id: town_room(s.cities) for s in AccountSnapshot.objects.filter(game_account__in=accounts)}


def _job_inputs(job) -> dict:
    try:
        data = json.loads(job.inputs_json or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _ranking_clock(highscore: dict) -> int | None:
    """Seconds until the ranking turns, counted down since it was read (None when unknown)."""
    left = int(highscore.get("time_left") or 0)
    read_at = int(highscore.get("updated_at") or 0)
    if left <= 0 or read_at <= 0:
        return None
    return max(0, left - max(0, int(timezone.now().timestamp()) - read_at))


def accounts_on(server_id: str) -> list:
    """The organization's active game accounts on that server (the request's scope applies)."""
    from apps.accounts.models import GameAccount

    return list(GameAccount.objects.filter(server_id=server_id, active=True, account__active=True)
                .select_related("account").order_by("account__label", "name"))


def eligible(server_id: str) -> list:
    """Accounts that can take part in the plan: a Pirate Fortress (to raid and be raided) and a Pirate Fortress job
    running (the agent acts through it)."""
    from apps.game.models import AccountSnapshot
    from apps.jobs.models import Job

    accounts = accounts_on(server_id)
    with_job = set(Job.objects.filter(game_account__in=accounts, action_code=PIRATE_ACTION)
                   .exclude(status__in=("cancelled", "canceled", "failed", "completed", "done"))
                   .values_list("game_account_id", flat=True))
    fortress = {s.game_account_id for s in AccountSnapshot.objects.filter(game_account__in=accounts)
                if (_fortress_city(s.cities) or {}).get("level", 0) >= 1}
    return [ga for ga in accounts if ga.pk in with_job and ga.pk in fortress]


def servers() -> list[str]:
    from apps.accounts.models import GameAccount

    return sorted(set(GameAccount.objects.filter(active=True).values_list("server_id", flat=True)))


def overview(server_id: str) -> dict:
    """Everything the piracy page shows for one server."""
    from apps.captcha.models import CaptchaChallenge
    from apps.game.models import AccountSnapshot
    from apps.game.services.attacks import is_pirate
    from apps.jobs.models import Job

    accounts = accounts_on(server_id)
    snapshots = {s.game_account_id: s for s in AccountSnapshot.objects.filter(game_account__in=accounts)}
    jobs = {}
    for job in (Job.objects.filter(game_account__in=accounts, action_code=PIRATE_ACTION)
                .exclude(status__in=("cancelled", "canceled")).order_by("game_account_id", "-created_at")):
        jobs.setdefault(job.game_account_id, job)
    captchas = defaultdict(int)
    for ga_id in CaptchaChallenge.objects.filter(game_account__in=accounts, status__in=("pending", "solving"),
                                                 captcha_type="piracy").values_list("game_account_id", flat=True):
        captchas[ga_id] += 1
    month = current_month(server_id)
    roles = month_roles(month) if month else {}

    rows, ranking, clock, raids = [], [], None, []
    lobby_size = defaultdict(int)
    for ga in accounts:
        lobby_size[ga.account_id] += 1
    for ga in accounts:
        snap = snapshots.get(ga.pk)
        base = (snap.base_snapshot or {}) if snap else {}
        highscore = base.get("piracy_highscore") or {}
        own = highscore.get("self_entry") or {}
        left = _ranking_clock(highscore)
        if left is not None and (clock is None or (highscore.get("updated_at") or 0) > clock[1]):
            clock = (left, highscore.get("updated_at") or 0)
            ranking = list(highscore.get("entries") or [])
        job = jobs.get(ga.pk)
        inputs = _job_inputs(job) if job else {}
        alert = base.get("attack_alert_state") or {}
        incoming = [e for e in (alert.get("events") or []) if isinstance(e, dict) and is_pirate(e)]
        pirates = int(alert.get("pirate_count") or len(incoming)) if isinstance(alert, dict) else 0
        for event in incoming:
            raids.append({"account": ga, "from": event.get("origin_player") or "?", "city": event.get("target_city") or "",
                          "eta": event.get("time_left_human") or "", "eta_sec": int(event.get("time_left_sec") or 0)})
        fortress = _fortress_city(snap.cities if snap else None)
        above = next((e for e in highscore.get("entries") or []
                      if own.get("place") and int(e.get("place") or 0) == int(own.get("place")) - 1), None)
        rows.append({
            "gap_up": (int(above.get("score") or 0) - int(own.get("score") or 0)) if above else None,
            "above_name": (above or {}).get("player_name") or "",
            "ga": ga, "lobby": ga.account.label, "fortress": fortress, "room": town_room(snap.cities if snap else None),
            "place": own.get("place"), "score": own.get("score"),
            "capture_points": inputs.get("last_capture_points"),
            "crew": inputs.get("last_complete_crew_points", inputs.get("last_crew_points")),
            "crew_converted": inputs.get("total_crew_converted"), "missions": inputs.get("total_missions_started"),
            "final_phase": bool(inputs.get("piracy_final_phase_active")),
            "special": (inputs.get("_targeted_phase") or ""), "special_target": inputs.get("_targeted_target_player_name") or "",
            "job": job, "pirates": pirates, "captchas": captchas.get(ga.pk, 0),
            "role": roles.get(ga.pk, ""),
        })
    own_names = {str(ga.name or "").strip().casefold() for ga in accounts}
    for entry in ranking:
        entry["own"] = str(entry.get("player_name") or "").strip().casefold() in own_names
    best = min((r["place"] for r in rows if r["place"]), default=None)
    raids.sort(key=lambda r: r["eta_sec"] or 10 ** 9)
    return {
        "server_id": server_id, "rows": rows, "ranking": ranking[:10], "clock": clock[0] if clock else None,
        "best_place": best, "points": sum(int(r["score"] or 0) for r in rows),
        "raids": raids, "pirates": sum(r["pirates"] for r in rows), "captchas": sum(r["captchas"] for r in rows),
        "with_job": sum(1 for r in rows if r["job"]), "month": month, "lobbies": len(lobby_size),
    }


# ── the month ──────────────────────────────────────────────────────────────────────────────────────────────────────

WIN_FACTOR = 1.1 / 0.9       # each side gets -10%..+10% on its crew: a sure win needs crew > target crew x 1.1 / 0.9
BONUS = 0.1
MIN_POINTS = 100             # fewer capture points than this are not worth a raid
PAIR_DAYS = 7                # the same raider takes from the same account once in this many days
ROUTE_HOURS = 6              # a raid decided and not sent in this time is dropped (the next free ship gets a new one)


def current_month(server_id: str):
    return PiracyMonth.objects.filter(server_id=server_id, status=PiracyMonth.OPEN).order_by("-starts_at").first()


def month_roles(month) -> dict:
    """game account id -> "campeã" | "passagem" | "origem"."""
    names = {PiracyMember.CHAMPION: "campeã", PiracyMember.PASSER: "passagem", PiracyMember.ORIGIN: "origem"}
    return {m.game_account_id: names[m.role] for m in month.members.all()}


def month_window(server_id: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """From now to when the ranking turns (the clock the Pirate Fortress job reads), or a month when unknown. With
    less than two days left the plan is for the next ranking: it starts when this one turns."""
    now = now or timezone.now()
    clock = overview_clock(server_id)
    if clock is not None and clock < 2 * 86400:
        start = now + timedelta(seconds=clock)
        return start, start + timedelta(days=MONTH_FALLBACK_DAYS)
    end = now + timedelta(seconds=clock) if clock else now + timedelta(days=MONTH_FALLBACK_DAYS)
    return now, end


def overview_clock(server_id: str) -> int | None:
    from apps.game.models import AccountSnapshot

    best = None
    for snap in AccountSnapshot.objects.filter(game_account__server_id=server_id, game_account__active=True):
        highscore = (snap.base_snapshot or {}).get("piracy_highscore") or {}
        left = _ranking_clock(highscore)
        if left is not None and (best is None or (highscore.get("updated_at") or 0) > best[1]):
            best = (left, highscore.get("updated_at") or 0)
    return best[0] if best else None


def _past_champions(server_id: str) -> set:
    return set(PiracyChampion.objects.filter(month__server_id=server_id).values_list("game_account_id", flat=True))


def pick_champions(accounts: list, server_id: str, rng: random.Random | None = None) -> list:
    """Two from different lobbies, drawn among the accounts that were not champions yet (all again once everyone was)."""
    rng = rng or random.Random()
    past = _past_champions(server_id)
    pool = [ga for ga in accounts if ga.pk not in past] or list(accounts)
    rng.shuffle(pool)
    chosen = []
    for ga in pool:
        if all(ga.account_id != c.account_id for c in chosen):
            chosen.append(ga)
        if len(chosen) == CHAMPIONS:
            break
    if len(chosen) < CHAMPIONS:                     # the not-yet ones are all of one lobby: complete from everyone
        for ga in sorted(accounts, key=lambda _g: rng.random()):
            if ga not in chosen and all(ga.account_id != c.account_id for c in chosen):
                chosen.append(ga)
                if len(chosen) == CHAMPIONS:
                    break
    return chosen


def build_groups(accounts: list, champions: list) -> list[dict]:
    """Split the other accounts between the champions: per group, passers (never the champion's lobby; small lobbies
    first, they bridge the big ones) and origins (any lobby: whoever takes them must be of another one)."""
    others = [ga for ga in accounts if ga not in champions]
    lobby_size = defaultdict(int)
    for ga in accounts:
        lobby_size[ga.account_id] += 1
    groups = [{"champion": c, "passers": [], "origins": []} for c in champions]
    for ga in sorted(others, key=lambda g: (lobby_size[g.account_id], g.name or "")):
        fit = [gr for gr in groups if gr["champion"].account_id != ga.account_id and len(gr["passers"]) < PASSERS_PER_GROUP
               and all(p.account_id != ga.account_id for p in gr["passers"])]
        if fit:
            min(fit, key=lambda gr: len(gr["passers"]))["passers"].append(ga)
    taken = {p.pk for gr in groups for p in gr["passers"]}
    for ga in [g for g in others if g.pk not in taken]:
        min(groups, key=lambda gr: len(gr["origins"]))["origins"].append(ga)
    return groups


@transaction.atomic
def plan_month(organization, server_id: str, *, champions: list | None = None, now: datetime | None = None,
               rng: random.Random | None = None, share_percent: int = 10, share_every_hours: int = 24,
               min_win_percent: int = 95):
    """Close the open month of that server and start a new one: champions, passers and origins of each group."""
    now = now or timezone.now()
    old = current_month(server_id)
    if old is not None:
        close_month(old, "substituído por um novo plano")
    accounts = eligible(server_id)
    if len({ga.account_id for ga in accounts}) < 2 or len(accounts) < 3:
        raise ValueError("Faltam contas com fortaleza pirata e Pirata Automático rodando (pelo menos 3, de 2 lobbys).")
    chosen = list(champions) if champions else pick_champions(accounts, server_id, rng)
    if len(chosen) != CHAMPIONS or chosen[0].account_id == chosen[1].account_id:
        raise ValueError("São 2 campeãs de lobbys diferentes.")
    if any(ga not in accounts for ga in chosen):
        raise ValueError("A campeã precisa ter fortaleza pirata e o Pirata Automático rodando.")
    starts, ends = month_window(server_id, now)
    month = PiracyMonth.objects.create(organization=organization, server_id=server_id, starts_at=starts, ends_at=ends,
                                       chosen_by="escolha" if champions else "sorteio",
                                       share_percent=max(0, min(100, int(share_percent))),
                                       share_every_hours=max(1, min(240, int(share_every_hours or 24))),
                                       min_win_percent=max(50, min(100, int(min_win_percent or 95))))
    for number, group in enumerate(build_groups(accounts, chosen), start=1):
        PiracyChampion.objects.create(month=month, game_account=group["champion"], group=number)
        PiracyMember.objects.create(month=month, game_account=group["champion"], role=PiracyMember.CHAMPION, group=number)
        for ga in group["passers"]:
            PiracyMember.objects.create(month=month, game_account=ga, role=PiracyMember.PASSER, group=number)
        for ga in group["origins"]:
            PiracyMember.objects.create(month=month, game_account=ga, role=PiracyMember.ORIGIN, group=number)
    return month


def close_month(month, reason: str = "encerrado") -> None:
    month.routes.filter(status=PiracyRoute.PLANNED).update(status=PiracyRoute.SKIPPED, message=reason[:255])
    month.status = PiracyMonth.CLOSED
    month.save(update_fields=["status", "updated_at"])


def expire(now: datetime | None = None) -> int:
    """A raid decided and not sent in ROUTE_HOURS is dropped; months past their end close."""
    now = now or timezone.now()
    skipped = PiracyRoute._base_manager.filter(status=PiracyRoute.PLANNED, due_until__lt=now).update(
        status=PiracyRoute.SKIPPED, message="não saiu a tempo")
    for month in PiracyMonth._base_manager.filter(status=PiracyMonth.OPEN, ends_at__lt=now):
        close_month(month, "o ranking virou")
    return skipped


# ── the agent's side ───────────────────────────────────────────────────────────────────────────────────────────────

def strength(game_account) -> dict:
    """What the Pirate Fortress job last read of this account: capture points and crew strength (None when unknown)."""
    from apps.jobs.models import Job

    job = (Job._base_manager.filter(game_account=game_account, action_code=PIRATE_ACTION).order_by("-created_at").first())
    inputs = _job_inputs(job) if job else {}
    crew = inputs.get("last_complete_crew_points", inputs.get("last_crew_points"))
    points = inputs.get("last_capture_points")
    return {"crew": int(crew) if crew is not None else None, "points": int(points) if points is not None else None}


def win_chance(raider_crew: int | None, target_crew: int | None) -> float | None:
    """The chance the raider wins: each side's crew gets an independent random -10%..+10%, the bigger wins.
    P(a(1+x) > b(1+y)) with x, y uniform — integrated over the target's crew (exact enough with 400 steps)."""
    if raider_crew is None or target_crew is None:
        return None
    if target_crew <= 0:
        return 1.0
    if raider_crew <= 0:
        return 0.0
    low_a, high_a = raider_crew * (1 - BONUS), raider_crew * (1 + BONUS)
    low_b, high_b = target_crew * (1 - BONUS), target_crew * (1 + BONUS)
    steps, total = 400, 0.0
    for i in range(steps):
        y = low_b + (high_b - low_b) * (i + 0.5) / steps
        total += min(1.0, max(0.0, (high_a - y) / (high_a - low_a)))
    return total / steps


def sure_win(raider_crew: int | None, target_crew: int | None, min_percent: int = 100) -> bool:
    chance = win_chance(raider_crew, target_crew)
    return chance is not None and chance * 100 >= min(100, max(1, min_percent)) - 1e-9


def _target_info(target) -> dict | None:
    from apps.game.models import AccountSnapshot

    snap = AccountSnapshot._base_manager.filter(game_account=target).first()
    base = (snap.base_snapshot or {}) if snap else {}
    highscore = base.get("piracy_highscore") or {}
    fortress = _fortress_city(snap.cities if snap else None)
    city_id = str((highscore.get("self_entry") or {}).get("target_city_id") or highscore.get("city_id") or (fortress or {}).get("id") or "")
    island_id = str((highscore.get("self_entry") or {}).get("target_island_id") or (fortress or {}).get("island_id") or "")
    if not city_id:
        return None
    return {"target_city_id": city_id, "target_island_id": island_id,
            "player_name": base.get("player_name") or target.name or ""}


def _outside_info(target) -> dict | None:
    """City of a target added by hand: as typed, or its fortress city from the rankings the accounts read."""
    from apps.game.models import AccountSnapshot

    if target.city_id:
        return {"target_city_id": target.city_id, "target_island_id": target.island_id, "player_name": target.player_name}
    wanted = target.player_name.strip().casefold()
    for snap in AccountSnapshot._base_manager.filter(game_account__server_id=target.server_id,
                                                      game_account__account__organization=target.organization):
        for entry in ((snap.base_snapshot or {}).get("piracy_highscore") or {}).get("entries") or []:
            if str(entry.get("player_name") or "").strip().casefold() == wanted and entry.get("target_city_id"):
                return {"target_city_id": str(entry["target_city_id"]), "target_island_id": str(entry.get("target_island_id") or ""),
                        "player_name": target.player_name}
    return None


def _payload(route, info) -> dict:
    return {"route_id": str(route.pk), "target_game_account_id": str(route.target_id or ""),
            "raider_lobby": str(route.raider.account_id), "target_lobby": str(route.target.account_id) if route.target_id else "",
            "stage": route.stage, "outside": bool(route.outside_id), "target_points": route.target_points, **info}


def next_for(game_account, now: datetime | None = None) -> dict:
    """What the Pirate Fortress job of this account does for the plan now:
    {"route": {...} | None, "hold": bool, "convert_all": bool, "share_percent": int, "share_every_hours": int}.
    Champions convert all their points (convert_all) and, after gathering, raid the targets added by hand. The others
    keep their points (hold), converting share_percent of them once every share_every_hours. route: whom to raid now —
    someone earlier on the way, of another lobby, surely beaten, not raided by it in the last 7 days, with the most
    capture points."""
    now = now or timezone.now()
    expire(now)
    member = (PiracyMember._base_manager.filter(game_account=game_account, month__status=PiracyMonth.OPEN,
                                                 month__starts_at__lte=now).select_related("month").first())
    if member is None:
        return {"route": None, "hold": False}
    month = member.month
    hold = member.role != PiracyMember.CHAMPION
    extra = {"convert_all": not hold, "share_percent": month.share_percent if hold else 0,
             "share_every_hours": month.share_every_hours}
    open_route = (PiracyRoute._base_manager.filter(raider=game_account, month=month, status=PiracyRoute.PLANNED, due_until__gt=now)
                  .select_related("raider__account", "target__account", "outside").first())
    if open_route is not None:
        info = _target_info(open_route.target) if open_route.target_id else _outside_info(open_route.outside)
        if info:
            return {"route": _payload(open_route, info), "hold": hold, **extra}
    if member.role == PiracyMember.ORIGIN:
        return {"route": None, "hold": hold, **extra}
    level = PiracyMember.LEVEL[member.role]
    mine = strength(game_account)
    week = max(1, int((now - month.starts_at).total_seconds() // (7 * 86400)) + 1)
    recent = set(PiracyRoute._base_manager.filter(raider=game_account, created_at__gte=now - timedelta(days=PAIR_DAYS))
                 .exclude(status=PiracyRoute.SKIPPED).values_list("target_id", flat=True))
    candidates = []
    for other in (PiracyMember._base_manager.filter(month=month, group=member.group)
                  .exclude(game_account=game_account).select_related("game_account__account")):
        ga = other.game_account
        if PiracyMember.LEVEL[other.role] >= level or ga.account_id == game_account.account_id or ga.pk in recent:
            continue
        theirs = strength(ga)
        if (theirs["points"] or 0) < MIN_POINTS or not sure_win(mine["crew"], theirs["crew"], month.min_win_percent):
            continue
        candidates.append((theirs["points"], theirs["crew"], ga))
    for points, crew, ga in sorted(candidates, key=lambda c: -c[0]):
        info = _target_info(ga)
        if not info:
            continue
        route = PiracyRoute.objects.create(month=month, group=member.group, week=week, stage=level, raider=game_account,
                                           target=ga, target_points=points, raider_crew=mine["crew"], target_crew=crew,
                                           due_from=now, due_until=now + timedelta(hours=ROUTE_HOURS))
        return {"route": _payload(route, info), "hold": hold, **extra}
    return {"route": _outside_route(month, member, game_account, mine, now, week), "hold": hold, **extra}


def _outside_route(month, member, game_account, mine, now, week) -> dict | None:
    """A target added by hand, raided by a champion (the strongest crew) — not twice by the same one in 7 days."""
    if member.role != PiracyMember.CHAMPION:
        return None
    recent = set(PiracyRoute._base_manager.filter(raider=game_account, created_at__gte=now - timedelta(days=PAIR_DAYS))
                 .exclude(status=PiracyRoute.SKIPPED).values_list("outside_id", flat=True))
    for target in PiracyTarget._base_manager.filter(organization=month.organization, server_id=month.server_id, active=True):
        if target.pk in recent:
            continue
        info = _outside_info(target)
        if not info:
            continue
        route = PiracyRoute.objects.create(month=month, group=member.group, week=week, stage=3, raider=game_account,
                                           outside=target, raider_crew=mine["crew"], due_from=now,
                                           due_until=now + timedelta(hours=ROUTE_HOURS))
        return _payload(route, info)
    return None


def report(route, status: str, message: str = "") -> None:
    """The agent says how the raid went: sent (on its way), done (won: the points came), failed (lost or not sent)."""
    now = timezone.now()
    if status == PiracyRoute.SENT:
        route.status, route.sent_at = status, now
    elif status in (PiracyRoute.DONE, PiracyRoute.FAILED):
        route.status, route.done_at = status, now
    else:
        return
    route.message = (message or "")[:255]
    route.save(update_fields=["status", "sent_at", "done_at", "message", "updated_at"])
