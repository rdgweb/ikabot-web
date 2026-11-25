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
from datetime import datetime, timedelta, timezone as datetime_timezone

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
        job = jobs.get(ga.pk)
        inputs = _job_inputs(job) if job else {}
        # the account's state can lose the ranking to another refresh: the Pirate Fortress job keeps its own reading
        own = highscore.get("self_entry") or inputs.get("piracy_highscore_self") or {}
        left = _ranking_clock(highscore)
        if left is not None and (clock is None or (highscore.get("updated_at") or 0) > clock[1]):
            clock = (left, highscore.get("updated_at") or 0)
            ranking = list(highscore.get("entries") or [])

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
MIN_POINTS = 100             # fewer capture points than this are not worth a raid (targets by hand)
DRAW_HOURS = 24              # a raid of the plan happens at a random time within this many hours
START_DELAY = timedelta(minutes=15)   # the plan starts this long after it is drawn
TURN_MARGIN = timedelta(minutes=30)   # the last raid before a ranking turn is drawn at least this long before it
PAIR_DAYS = 3                # the same raider takes from the same account once in this many days (N-217: was 7)
CREW_BONUS_MAX = 1400        # N-218: crew strength the held capture points add (the game's completeCrewPoints has it)
CREW_BONUS_FULL = 7000       # points that give the bonus (Rodrigo; measured: all or nothing)
ROTATE_DAYS = 3              # N-217: passers and origins swap every this many days (the ranking runs 21 days)
SHARE_JITTER_DOWN, SHARE_JITTER_UP = 2, 1   # N-255: an origin's share is drawn this much below/above the plan's (3% -> 1-4%)
BALANCE_SHARE = 0.25         # N-217: a champion this far ahead of the other (share of its own take, at least
                             # min_points) leaves a target both can take to the other one
ROUTE_HOURS = 6              # a raid decided and not sent in this time is dropped (the next free ship gets a new one)


def current_month(server_id: str):
    return PiracyMonth.objects.filter(server_id=server_id, status=PiracyMonth.OPEN).order_by("-starts_at").first()


def rotation_index(month, now: datetime | None = None) -> int:
    """Which turn of the role swap the month is in (0 before it starts). A ranking turn inside the plan ends the first
    turn; from it the roles swap every rotate_days."""
    now = now or timezone.now()
    step = max(1, month.rotate_days) * 86400
    if now < month.starts_at:
        return 0
    if month.turn_at:
        if now < month.turn_at:
            return 0
        return 1 + int((now - month.turn_at).total_seconds() // step)
    return int((now - month.starts_at).total_seconds() // step)


def next_rotation(month, now: datetime | None = None):
    now = now or timezone.now()
    step = timedelta(days=max(1, month.rotate_days))
    if month.turn_at:
        if now < month.turn_at:
            return month.turn_at
        return month.turn_at + step * (rotation_index(month, now))
    return month.starts_at + step * (rotation_index(month, now) + 1)


def members_now(month, now: datetime | None = None) -> list:
    """The month's accounts with their role now: the champions stay; in each group the passers change turn by turn
    (every rotate_days), as many as the group started with. N-215: each turn picks the passers among the accounts that
    may pass points on (a champion of another lobby can take theirs), so that every origin keeps someone of another
    lobby to take its points, giving the turn to who passed the least. N-255: among those, the ones that need to convert
    the least to beat their origins; a turn's passers are decided once and kept in month.turns (they never change in
    the middle of a turn when the crews change)."""
    from types import SimpleNamespace

    turn = rotation_index(month, now)
    stored = list(month.members.select_related("game_account__account").order_by("group", "id"))
    saved_turns = dict(month.turns or {})
    changed = False
    out = []
    for group in sorted({m.group for m in stored}):
        of = [m for m in stored if m.group == group]
        champions = [m.game_account for m in of if m.role == PiracyMember.CHAMPION]
        for ga in champions:
            out.append(SimpleNamespace(game_account=ga, game_account_id=ga.pk, group=group, role=PiracyMember.CHAMPION))
        others = [m.game_account for m in of if m.role != PiracyMember.CHAMPION]
        passers = [m.game_account for m in of if m.role == PiracyMember.PASSER]
        counts = {ga.pk: (1 if ga in passers else 0) for ga in others}
        cost = None
        for number in range(1, turn + 1):
            key = f"{group}:{number}"
            if key in saved_turns:
                kept = {str(pk) for pk in saved_turns[key]}
                passers = [ga for ga in others if str(ga.pk) in kept]
            else:
                if cost is None:
                    cost = _conversion_cost(month, others)
                passers = _next_passers(champions, others, passers, counts, cost)
                saved_turns[key] = [str(ga.pk) for ga in passers]
                changed = True
            for ga in passers:
                counts[ga.pk] += 1
        chosen = {ga.pk for ga in passers}
        for ga in others:
            out.append(SimpleNamespace(game_account=ga, game_account_id=ga.pk, group=group,
                                       role=PiracyMember.PASSER if ga.pk in chosen else PiracyMember.ORIGIN))
    if changed and month.pk:
        month.turns = saved_turns
        PiracyMonth._base_manager.filter(pk=month.pk).update(turns=saved_turns)
    return out


def at_raid(month, crew: int | None, points: int | None) -> int | None:
    """N-255: an account's strength when it is raided — it holds min_points or more then (the bonus of those)."""
    if crew is None:
        return None
    held = int(points or 0)
    return crew - crew_bonus(held) + crew_bonus(max(held, month.min_points))


def _conversion_cost(month, accounts: list):
    """N-255: cost(passers, origins) — the capture points the passers convert, all together, so each beats the origin
    it would take from first (the most points for the least conversion); an origin it cannot beat costs a lot."""
    read = {ga.pk: strength(ga) for ga in accounts}
    ratio = ratio_for(month.min_win_percent)

    def one(passer, origins) -> int:
        mine = read.get(passer.pk) or {}
        if mine.get("crew") is None or mine.get("points") is None:
            return 10 ** 6
        best = None
        for o in origins:
            theirs = read.get(o.pk) or {}
            if o.account_id == passer.account_id or theirs.get("crew") is None:
                continue
            need = int(at_raid(month, theirs["crew"], theirs.get("points")) * ratio) + 1
            points = convert_for(mine["crew"] - crew_bonus(mine["points"]), int(mine["points"]), need, None)
            if points is not None and (best is None or points < best):
                best = points
        return best if best is not None else 10 ** 6

    return lambda passers, origins: sum(one(p, origins) for p in passers)


def _as_list(champions) -> list:
    if champions is None:
        return []
    return list(champions) if isinstance(champions, (list, tuple)) else [champions]


def _next_passers(champions, others: list, current: list, counts: dict, cost=None) -> list:
    """The passers of the next turn of one group (see members_now); counts: turns each account has passed so far;
    cost(passers, origins): the points they would convert (N-255), the lower the better among the fairest."""
    from itertools import combinations

    champions = _as_list(champions)
    if not current:
        return []
    able = [ga for ga in others if not champions or any(c.account_id != ga.account_id for c in champions)]
    size = min(len(current), len(able))
    if not size:
        return current
    now_ids = {ga.pk for ga in current}

    def key(combo):
        ids = {ga.pk for ga in combo}
        links = chain(champions, list(combo), [ga for ga in others if ga.pk not in ids])
        served = sum(1 for o in others if o.pk not in ids and any(t.pk in ids for t in links["takers"].get(o.pk, [])))
        spend = cost(list(combo), [ga for ga in others if ga.pk not in ids]) if cost else 0
        return (len(links["blocked"]), len(links["idle"]), sum(counts.get(ga.pk, 0) for ga in combo), spend, ids == now_ids,
                -served, sorted(str(ga.pk) for ga in combo))

    options = list(combinations(sorted(able, key=lambda ga: (ga.name or "", str(ga.pk))), size))[:3000]
    best = min(options, key=key)
    if len(chain(champions, list(best), [ga for ga in others if ga not in best])["blocked"]) > \
            len(chain(champions, current, [ga for ga in others if ga not in current])["blocked"]):
        return current                                   # no choice keeps every origin reachable: the passers stay
    return list(best)


def chain(champions, passers: list, origins: list) -> dict:
    """N-215: who may take whose points in a group, by lobby alone (the game never lets two accounts of one lobby raid
    each other). champions: one account or a list (N-217: one pool with both champions). takers[origin pk]: the
    passers and champions of another lobby; sources[passer pk]: the origins of another lobby. blocked: origins nobody
    may take; idle: passers with no origin to take from; wrong: passers no champion may take from (all of its lobby)."""
    champions = _as_list(champions)
    takers = {}
    for o in origins:
        takers[o.pk] = [p for p in passers if p.account_id != o.account_id] + \
                       [c for c in champions if c.account_id != o.account_id]
    sources = {p.pk: [o for o in origins if o.account_id != p.account_id] for p in passers}
    return {"takers": takers, "sources": sources,
            "blocked": [o for o in origins if not takers[o.pk]],
            "idle": [p for p in passers if not sources[p.pk]],
            "wrong": [p for p in passers if champions and all(c.account_id == p.account_id for c in champions)]}


def month_roles(month) -> dict:
    """game account id -> "campeã" | "passagem" | "origem" (now)."""
    names = {PiracyMember.CHAMPION: "campeã", PiracyMember.PASSER: "passagem", PiracyMember.ORIGIN: "origem"}
    return {m.game_account_id: names[m.role] for m in members_now(month)}


def month_window(server_id: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """The plan starts START_DELAY after it is drawn and runs until the ranking turns (the clock the Pirate Fortress job
    reads), or a month when unknown. With less than two days left it runs on through the next ranking too."""
    now = now or timezone.now()
    start = now + START_DELAY
    clock = overview_clock(server_id)
    if clock is not None and clock < 2 * 86400:
        return start, now + timedelta(seconds=clock) + timedelta(days=MONTH_FALLBACK_DAYS)
    end = now + timedelta(seconds=clock) if clock else now + timedelta(days=MONTH_FALLBACK_DAYS)
    return start, end


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


def build_groups(accounts: list, champions: list, crews: dict | None = None) -> list[dict]:
    """N-217: one pool — the two champions (of different lobbies) and every other account. The points go origin ->
    passer -> champion and the game never lets two accounts of one lobby raid each other; with the champions of two
    lobbies every account has a champion of another lobby. Picks the passers (up to PASSERS_PER_GROUP) so that every
    passer has origins of another lobby, preferring accounts that serve more origins and leaving room for the roles to
    take turns (members_now)."""
    crews = crews or {}
    others = sorted([ga for ga in accounts if ga not in champions], key=lambda ga: (ga.name or "", str(ga.pk)))
    if not champions:
        return []

    def odds(a, b):
        value = win_chance(crews.get(a.pk), crews.get(b.pk))
        return value if value is not None else 0.5

    def score(passers: list) -> float:
        origins = [ga for ga in others if ga not in passers]
        links = chain(champions, passers, origins)
        total = -1000 * len(links["wrong"]) - 100 * len(links["blocked"]) - 20 * len(links["idle"])
        total += 15 * min(PASSERS_PER_GROUP, len(passers) - len(links["idle"]))
        served = sum(1 for o in origins if any(t in passers for t in links["takers"][o.pk]))
        total -= 25 * max(0, len(passers) - served)
        able = sum(1 for ga in others if any(c.account_id != ga.account_id for c in champions))
        if passers and able > len(passers):
            total += 20
        for p in passers:
            total += 5 * max((odds(c, p) for c in champions if c.account_id != p.account_id), default=0)
        return total

    from itertools import combinations

    best, best_score = [], score([])
    for size in range(1, min(PASSERS_PER_GROUP, len(others)) + 1):
        for combo in list(combinations(others, size))[:3000]:
            value = score(list(combo))
            if value > best_score + 1e-9:
                best, best_score = list(combo), value
    origins = [ga for ga in others if ga not in best]
    return [{"champions": list(champions), "champion": champions[0], "members": best + origins,
             "passers": best, "origins": origins}]


@transaction.atomic
def plan_month(organization, server_id: str, *, champions: list | None = None, now: datetime | None = None,
               rng: random.Random | None = None, share_percent: int = 3, share_every_hours: int = 24,
               min_win_percent: int = 95, min_points: int = 10000, pair_days: int = 3, rotate_days: int | None = None):
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
    clock = overview_clock(server_id)
    turn_at = (now + timedelta(seconds=clock)) if clock is not None and clock < 2 * 86400 else None
    month = PiracyMonth.objects.create(organization=organization, server_id=server_id, starts_at=starts, ends_at=ends,
                                       turn_at=turn_at,
                                       chosen_by="escolha" if champions else "sorteio",
                                       share_percent=max(0, min(100, int(share_percent))),
                                       share_every_hours=max(1, min(240, int(share_every_hours or 24))),
                                       min_win_percent=max(50, min(100, int(min_win_percent or 95))),
                                       min_points=max(100, int(min_points or 10000)),
                                       rotate_days=max(1, min(30, int(rotate_days or ROTATE_DAYS))),
                                       pair_days=max(1, min(30, int(pair_days or 3))))
    crews = {ga.pk: strength(ga)["crew"] for ga in accounts}
    for number, group in enumerate(build_groups(accounts, chosen, crews), start=1):
        for champion in group["champions"]:
            PiracyChampion.objects.create(month=month, game_account=champion, group=number)
            PiracyMember.objects.create(month=month, game_account=champion, role=PiracyMember.CHAMPION, group=number)
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
    for month in PiracyMonth._base_manager.filter(status=PiracyMonth.OPEN, ends_at__lt=now).select_related("organization"):
        _roll_over(month, now)
    return skipped


def _roll_over(month, now) -> None:
    """N-255: the month ends at the ranking turn — the next one starts at once, with champions drawn among the accounts
    that were not champions yet and the same rules, so no account is ever left out of a plan (it would play by its
    job's own mode, converting all). With too few accounts it only closes."""
    from django.db import transaction

    from apps.orgs.scope import activate, deactivate

    token = activate(month.organization)
    try:
        with transaction.atomic():
            close_month(month, "o ranking virou")
            try:
                plan_month(month.organization, month.server_id, now=now, share_percent=month.share_percent,
                           share_every_hours=month.share_every_hours, min_win_percent=month.min_win_percent,
                           min_points=month.min_points, pair_days=month.pair_days, rotate_days=month.rotate_days)
            except ValueError:
                pass                                        # not enough accounts: the month only closes
    finally:
        deactivate(token)


# ── what a champion can gather ─────────────────────────────────────────────────────────────────────────────────────

def upcoming(month, rows_by_ga: dict) -> dict:
    """The next raids of the plan and the accounts over the points limit still without a raid — N-218: each with who
    may take it and why it has not yet (raids are asked as soon as the raider's job runs)."""
    planned = list(month.routes.filter(status__in=(PiracyRoute.PLANNED, PiracyRoute.SENT))
                   .select_related("raider", "target", "outside").order_by("due_from"))
    busy = {r.target_id for r in planned if r.target_id}
    raiding = {r.raider_id for r in planned}
    members = members_now(month)
    level = {m.game_account_id: PiracyMember.LEVEL[m.role] for m in members}
    group = {m.game_account_id: m.group for m in members}
    waiting = []
    for ga_id, row in rows_by_ga.items():
        if row.get("role") not in ("passagem", "origem") or (row.get("capture_points") or 0) < month.min_points or ga_id in busy:
            continue
        ga = row["ga"]
        best = None
        for m in members:
            raider = m.game_account
            if (m.group != group.get(ga_id) or level[raider.pk] <= level.get(ga_id, 0)
                    or raider.account_id == ga.account_id):
                continue
            chance = win_chance((rows_by_ga.get(raider.pk) or {}).get("crew"), row.get("crew"))
            if chance is not None and (best is None or chance > best[1]):
                best = (raider, chance)
        if best is None:
            row["why"] = "ninguém de outro lobby acima dela"
        elif best[1] * 100 >= month.min_win_percent - 1e-9:
            row["why"] = (f"{best[0].name} vence ({round(best[1] * 100)}%)"
                          + (" · ocupada com outro roubo" if best[0].pk in raiding else " · rouba na próxima rodada dela"))
        else:
            row["why"] = f"melhor: {best[0].name} com {round(best[1] * 100)}% (precisa {month.min_win_percent}%)"
        waiting.append(row)
    return {"planned": planned, "waiting": sorted(waiting, key=lambda r: -(r.get("capture_points") or 0))}


def projection(month, rows_by_ga: dict) -> dict:
    """Per group: how many points the champion can gather this month from what the accounts hold now. A lost raid takes
    nothing (only the pair's week), so each account adds its points x the chance they reach the champion: straight
    (champion beats it) or through a passer (passer beats it, champion beats the passer) — each hop with at least the
    month's minimum chance, the better way counted."""
    out = {}
    members = members_now(month)
    minimum = month.min_win_percent / 100

    def crew(ga):
        return (rows_by_ga.get(ga.pk) or {}).get("crew")

    def points(ga):
        return int((rows_by_ga.get(ga.pk) or {}).get("capture_points") or 0)

    def chance(a, b):
        if a.account_id == b.account_id:
            return 0.0
        value = win_chance(crew(a), crew(b))
        return value if value is not None and value >= minimum - 1e-9 else 0.0

    for group in sorted({m.group for m in members}):
        of = [m for m in members if m.group == group]
        champions = [m.game_account for m in of if m.role == PiracyMember.CHAMPION]
        if not champions:
            continue
        champion = champions[0]
        passers = [m.game_account for m in of if m.role == PiracyMember.PASSER]
        detail, expected, held = [], 0.0, 0
        for m in of:
            if m.role == PiracyMember.CHAMPION:
                continue
            ga = m.game_account
            held += points(ga)
            best, way = 0.0, "direto"
            for c in champions:
                if chance(c, ga) > best:
                    best, way, champion = chance(c, ga), f"direto para {c.name}" if len(champions) > 1 else "direto", c
            if m.role == PiracyMember.ORIGIN:
                for passer in passers:
                    for c in champions:
                        via = chance(passer, ga) * chance(c, passer)
                        if via > best:
                            best, way, champion = via, f"por {passer.name}" + (f" para {c.name}" if len(champions) > 1 else ""), c
            expected += points(ga) * best
            if not best:                                   # say why its points do not reach the champion
                raw = win_chance(crew(champion), crew(ga))
                if crew(ga) is None or crew(champion) is None:
                    way = "tripulação não lida"
                elif champion.account_id == ga.account_id and not passers:
                    way = "mesmo lobby da campeã"
                elif champion.account_id == ga.account_id:
                    way = "mesmo lobby; passagem não vence"
                else:
                    way = f"campeã vence só {round((raw or 0) * 100)}%"
            detail.append({"ga": ga, "points": points(ga), "chance": round(best * 100), "way": way})
        out[group] = {"expected": int(expected), "held": held, "detail": sorted(detail, key=lambda d: -d["points"])}
    return out


# ── targets ────────────────────────────────────────────────────────────────────────────────────────────────────────

def search_targets(server_id: str, query: str, limit: int = 20) -> list[dict]:
    """Cities with a Pirate Fortress (the game offers a piracy raid on them) of the latest world dump of the server,
    by player, alliance or city name; the organization's own players left out."""
    from apps.worldintel.models import WorldDump, WorldDumpCity
    from django.db.models import Q

    query = (query or "").strip()
    if len(query) < 2:
        return []
    dump = WorldDump.objects.filter(game_account__server_id=server_id).order_by("-captured_at", "-created_at").first()
    if dump is None:
        return []
    own = {str(ga.name or "").strip().casefold() for ga in accounts_on(server_id)}
    rows = []
    cities = (WorldDumpCity._base_manager.filter(dump=dump).filter(
        Q(owner_name__icontains=query) | Q(ally_tag__icontains=query) | Q(name__icontains=query))
        .select_related("island").order_by("owner_name", "name")[:400])
    for city in cities:
        actions = city.actions_json or {}
        has_piracy = ("piracy_raid" in actions) if isinstance(actions, (dict, list)) else False
        if not has_piracy or str(city.owner_name or "").strip().casefold() in own:
            continue
        rows.append({"player": city.owner_name, "ally": city.ally_tag, "city": city.name, "city_id": city.game_city_id,
                     "island_id": city.island.island_id if city.island_id else "", "x": city.island.x if city.island_id else "",
                     "y": city.island.y if city.island_id else "", "level": city.level})
        if len(rows) >= limit:
            break
    return rows


def marked_targets(organization, server_id: str) -> list[str]:
    """Players marked as piracy targets in Inteligência — suggested on the page."""
    from apps.worldintel.services.marks import marks_of

    return sorted(marks_of(organization, server_id, "pirate_target").values_list("owner_name", flat=True))


# ── the agent's side ───────────────────────────────────────────────────────────────────────────────────────────────

def strength(game_account) -> dict:
    """What the Pirate Fortress job last read of this account: capture points and crew strength (None when unknown)."""
    from apps.jobs.models import Job

    job = (Job._base_manager.filter(game_account=game_account, action_code=PIRATE_ACTION).order_by("-created_at").first())
    inputs = _job_inputs(job) if job else {}
    crew = inputs.get("last_complete_crew_points", inputs.get("last_crew_points"))
    points = inputs.get("last_capture_points")
    return {"crew": int(crew) if crew is not None else None, "points": int(points) if points is not None else None}


def crew_bonus(points: int | None) -> int:
    """N-218: the crew strength the capture points held add — the game's total strength (completeCrewPoints) is the
    base crew plus this. Converting points into crew takes it away; taking points in a raid brings it."""
    # measured 25/11 (strength - base - crew bought): 0 with 4.634 and 5.769 points, 1.400 with 10.179 and more —
    # all or nothing from CREW_BONUS_FULL, as Rodrigo said
    return CREW_BONUS_MAX if int(points or 0) >= CREW_BONUS_FULL else 0


def convert_for(base: int, points: int, floor: int | None, cap_base: int | None, factor: int = 10) -> int | None:
    """N-218: the fewest points to convert so the strength (base + crew bought + bonus of the points left) reaches
    floor, never taking the base above cap_base; 0 when already there, None when it cannot (each 10 points give 1 of
    crew but, below CREW_BONUS_FULL, cost 2 of bonus: converting there only lowers the strength)."""
    if floor is None:
        return 0
    factor = max(1, factor)
    for crew in range(0, max(0, points) // factor + 1):
        if cap_base is not None and base + crew > cap_base:
            return None
        if base + crew + crew_bonus(points - crew * factor) >= floor:
            return crew * factor
    return None


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


def ratio_for(min_percent: int) -> float:
    """How many times bigger the raider's crew must be to win with at least that chance."""
    low, high = 0.8, WIN_FACTOR
    for _ in range(40):
        middle = (low + high) / 2
        if (win_chance(middle * 10000, 10000) or 0) * 100 >= min(100, max(1, min_percent)):
            high = middle
        else:
            low = middle
    return high


def budgets(month, crews: dict, points: dict | None = None) -> dict:
    """Per account of the month, the crew it may have (cap: its raider still wins with the month's chance) and the crew
    it needs (floor: a passer beats its origins) — what decides how much it can convert. crews: game account id -> crew.
    The champion has neither (it converts all, and gathers in its final phase)."""
    ratio = ratio_for(month.min_win_percent)
    members = members_now(month)
    points = points or {}
    out = {}

    def at_raid(ga):
        """N-218: the strength of an account when it is raided — it holds min_points or more then."""
        crew = crews.get(ga.pk)
        if crew is None or ga.pk not in points:
            return crew
        held = points.get(ga.pk) or 0
        return crew - crew_bonus(held) + crew_bonus(max(held, month.min_points))
    for group in {m.group for m in members}:
        of = [m for m in members if m.group == group]
        champions = [m.game_account for m in of if m.role == PiracyMember.CHAMPION]
        passers = [m.game_account for m in of if m.role == PiracyMember.PASSER]
        origins = [m.game_account for m in of if m.role == PiracyMember.ORIGIN]

        def raiders_of(ga, role):
            mine = [c for c in champions if c.account_id != ga.account_id]
            if role == PiracyMember.PASSER:
                return mine
            return [p for p in passers if p.account_id != ga.account_id] + mine

        for m in of:
            if m.role == PiracyMember.CHAMPION:
                continue
            ga = m.game_account
            raider_crews = [crews.get(r.pk) for r in raiders_of(ga, m.role) if crews.get(r.pk) is not None]
            cap = int(max(raider_crews) / ratio) if raider_crews else None
            floor = None
            if m.role == PiracyMember.PASSER:
                # the crew to beat its origins — only the ones it can beat while its champion still beats it (cap wins)
                needs = sorted(int(at_raid(o) * ratio) + 1 for o in origins
                               if o.account_id != ga.account_id and at_raid(o) is not None)
                within = [n for n in needs if cap is None or n <= cap]
                floor = within[-1] if within else None
            # N-218: the base crew (strength without the bonus) it may reach: when raided it holds min_points or
            # more, and a passer holds its origins' points — so the whole bonus counts on top of it
            held = points.get(ga.pk)
            bonus_then = CREW_BONUS_MAX if m.role == PiracyMember.PASSER else crew_bonus(max(held or 0, month.min_points))
            cap_base = cap - bonus_then if cap is not None else None
            base = crews.get(ga.pk) - crew_bonus(held) if crews.get(ga.pk) is not None and held is not None else None
            out[ga.pk] = {"cap": cap, "floor": floor, "crew": crews.get(ga.pk), "cap_base": cap_base, "base": base}
    return out


def _final_phase(game_account) -> bool:
    """The account's Pirate Fortress job is in the ranking's final phase (it says so in what it last read)."""
    from apps.jobs.models import Job

    job = Job._base_manager.filter(game_account=game_account, action_code=PIRATE_ACTION).order_by("-created_at").first()
    return bool(_job_inputs(job).get("piracy_final_phase_active")) if job else False


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
            "stage": route.stage, "outside": bool(route.outside_id), "target_points": route.target_points,
            "target_name": route.target_name, **info}


def plan_for(game_account, now: datetime | None = None, fresh: dict | None = None) -> dict | None:
    """The account's part in the month's plan now, read only (nothing drawn): None when it is in no running plan.
    Champions convert all; the others hold their points, converting share_percent once every share_every_hours up to
    crew_cap (in the final phase of the ranking nobody converts)."""
    now = now or timezone.now()
    member = (PiracyMember._base_manager.filter(game_account=game_account, month__status=PiracyMonth.OPEN,
                                                 month__paused=False).select_related("month").order_by("month__starts_at").first())
    if member is None:
        return None
    month = member.month
    if month.starts_at > now:
        # N-216 (23/11): drawn but not started yet (the START_DELAY after a draw or a regroup): the account already
        # belongs to the plan, so it keeps its points — before this it fell back to its job's own mode (convert all)
        return {"member": member, "month": month, "members": [], "role": member.role, "hold": True, "final": False,
                "waiting": True, "convert_all": False, "share_percent": 0, "share_every_hours": month.share_every_hours,
                "crew_cap": None, "crew_floor": None}
    now_members = members_now(month, now)
    role = next((m.role for m in now_members if m.game_account_id == game_account.pk), member.role)
    hold = role != PiracyMember.CHAMPION
    out = {"member": member, "month": month, "members": now_members, "role": role, "hold": hold, "final": False,
           "convert_all": not hold, "share_percent": month.share_percent if role == PiracyMember.ORIGIN else 0,
           "share_jitter": [SHARE_JITTER_DOWN, SHARE_JITTER_UP], "preconvert_points": 0, "preconvert_target": "",
           "share_every_hours": month.share_every_hours, "crew_cap": None, "crew_floor": None, "base_cap": None,
           "bonus_max": CREW_BONUS_MAX, "bonus_full": CREW_BONUS_FULL}
    if hold:
        read = {m.game_account_id: strength(m.game_account) for m in now_members if m.group == member.group}
        if fresh:
            read[game_account.pk] = {**read.get(game_account.pk, {}), **fresh}
        mine_budget = budgets(month, {k: v["crew"] for k, v in read.items()},
                              {k: v["points"] for k, v in read.items() if v["points"] is not None}).get(game_account.pk) or {}
        out.update(crew_cap=mine_budget.get("cap"), crew_floor=None, base_cap=mine_budget.get("cap_base"))
        if role == PiracyMember.PASSER:
            # N-255: the passer converts AHEAD, at its turn, the least that beats the origin it takes from first (the
            # most points for the least conversion) — so the raid goes out as soon as the origin has the points
            best = _best_target(month, game_account, read.get(game_account.pk) or {}, now_members, member.group,
                                out["base_cap"], now)
            if best:
                out.update(preconvert_points=best["convert"], preconvert_target=best["ga"].name)
        # in the final phase of the ranking (the points count for the position at the turn) nobody converts
        if _final_phase(game_account):
            out.update(share_percent=0, crew_floor=None, final=True, preconvert_points=0)
    return out


def _convert_to_win(month, mine: dict, theirs: dict, base_cap) -> int | None:
    """N-254: the capture points a passer converts so it beats this account with the month's chance (0: it already
    does; None: no amount does). Only the points above the bonus limit raise the strength, and the base never goes
    above what lets its champion still beat it (base_cap)."""
    if mine.get("crew") is None or theirs.get("crew") is None:
        return None
    if sure_win(mine["crew"], theirs["crew"], month.min_win_percent):
        return 0
    if mine.get("points") is None:
        return None
    need = int(theirs["crew"] * ratio_for(month.min_win_percent)) + 1
    return convert_for(mine["crew"] - crew_bonus(mine["points"]), int(mine["points"]), need, base_cap)


def next_for(game_account, now: datetime | None = None, fresh: dict | None = None) -> dict:
    """What the Pirate Fortress job of this account does for the plan now:
    {"route": {...} | None, "hold": bool, "convert_all": bool, "share_percent": int, "share_every_hours": int}.
    Champions convert all their points (convert_all) and, after gathering, raid the targets added by hand. The others
    keep their points (hold), converting share_percent of them once every share_every_hours. route: whom to raid now —
    someone earlier on the way, of another lobby, surely beaten, not raided by it in the last 7 days, with the most
    capture points."""
    now = now or timezone.now()
    expire(now)
    fresh = {k: int(v) for k, v in (fresh or {}).items() if v is not None}
    plan = plan_for(game_account, now, fresh)
    if plan is None:
        return {"route": None, "hold": False}
    member, month, now_members, role, hold = plan["member"], plan["month"], plan["members"], plan["role"], plan["hold"]
    extra = {k: plan.get(k) for k in ("convert_all", "share_percent", "share_every_hours", "crew_cap", "crew_floor",
                                      "base_cap", "bonus_max", "bonus_full", "share_jitter", "preconvert_points",
                                      "preconvert_target")}
    if plan.get("waiting"):
        return {"route": None, "hold": True, **extra, "starts_at": int(month.starts_at.timestamp())}
    open_route = (PiracyRoute._base_manager.filter(raider=game_account, month=month, status=PiracyRoute.PLANNED, due_until__gt=now)
                  .select_related("raider__account", "target__account", "outside").order_by("due_from").first())
    if open_route is not None:
        if open_route.due_from > now:                       # drawn for later: the ship must be free then
            return {"route": None, "hold": hold, **extra, "route_at": int(open_route.due_from.timestamp())}
        info = _target_info(open_route.target) if open_route.target_id else _outside_info(open_route.outside)
        if info:
            payload = _payload(open_route, info)
            if open_route.target_id and role == PiracyMember.PASSER:
                # N-254: again with the numbers of now — what is still missing (0 once the conversion came in)
                convert = _convert_to_win(month, {**strength(game_account), **fresh}, strength(open_route.target), plan.get("base_cap"))
                if convert is None:
                    PiracyRoute._base_manager.filter(pk=open_route.pk).update(status=PiracyRoute.SKIPPED,
                                                                              message="a passagem não vence mais esse alvo")
                    open_route = None
                else:
                    payload["convert_points"] = convert
            if open_route is not None:
                return {"route": payload, "hold": hold, **extra}
    if role == PiracyMember.ORIGIN:
        return {"route": None, "hold": hold, **extra}
    level = PiracyMember.LEVEL[role]
    mine = {**strength(game_account), **fresh}
    week = max(1, int((now - month.starts_at).total_seconds() // (7 * 86400)) + 1)
    recent = set(PiracyRoute._base_manager.filter(raider=game_account, created_at__gte=now - timedelta(days=month.pair_days or PAIR_DAYS))
                 .exclude(status=PiracyRoute.SKIPPED).values_list("target_id", flat=True))
    taken = set(PiracyRoute._base_manager.filter(month=month, status__in=(PiracyRoute.PLANNED, PiracyRoute.SENT))
                .values_list("target_id", flat=True))           # one raid on an account at a time
    candidates = []
    for other in [m for m in now_members if m.group == member.group and m.game_account_id != game_account.pk]:
        ga = other.game_account
        if (PiracyMember.LEVEL[other.role] >= level or ga.account_id == game_account.account_id
                or ga.pk in recent or ga.pk in taken):
            continue
        theirs = strength(ga)
        if (theirs["points"] or 0) < month.min_points:
            continue
        if role == PiracyMember.PASSER:
            # N-254: a passer that does not beat it yet converts what is missing first (the fine control)
            convert = _convert_to_win(month, mine, theirs, plan.get("base_cap"))
            if convert is None:
                continue
        else:
            if not sure_win(mine["crew"], theirs["crew"], month.min_win_percent):
                continue
            convert = 0
        if role == PiracyMember.CHAMPION and _leave_to_other_champion(month, game_account, ga, theirs, now_members, member.group):
            continue
        candidates.append((theirs["points"], convert, theirs["crew"], ga))
    # N-218: as soon as possible (a time drawn within 24h left the points idle and open to others) — but never at or
    # after the ranking turn: then every account's capture points go back to 0 and a raid takes nothing
    if month.turn_at and now < month.turn_at and now >= month.turn_at - TURN_MARGIN:
        return {"route": None, "hold": hold, **extra}
    # N-255: the most points for the least conversion first (net gain)
    for points, convert, crew, ga in sorted(candidates, key=lambda c: (-(c[0] - c[1]), c[1])):
        if not _target_info(ga):
            continue
        when = now
        # a conversion first takes its time: the raid waits for it
        until = when + timedelta(hours=ROUTE_HOURS * (2 if convert else 1))
        if month.turn_at and now < month.turn_at:
            until = min(until, month.turn_at)
        PiracyRoute.objects.create(month=month, group=member.group, week=week, stage=level, raider=game_account,
                                   target=ga, target_points=points, raider_crew=mine["crew"], target_crew=crew,
                                   due_from=when, due_until=until)
        route = PiracyRoute._base_manager.filter(raider=game_account, target=ga, status=PiracyRoute.PLANNED).order_by("-created_at").first()
        return {"route": {**_payload(route, _target_info(ga)), "convert_points": convert}, "hold": hold, **extra}
    return {"route": _outside_route(month, member, game_account, mine, now, week, role), "hold": hold, **extra}


def pair_release(month, raider, target, now: datetime | None = None):
    """N-255: when this raider may take from this account again (None: it may now)."""
    now = now or timezone.now()
    last = (PiracyRoute._base_manager.filter(raider=raider, target=target, created_at__gte=now - timedelta(days=month.pair_days or PAIR_DAYS))
            .exclude(status=PiracyRoute.SKIPPED).order_by("-created_at").first())
    return last.created_at + timedelta(days=month.pair_days or PAIR_DAYS) if last else None


def _best_target(month, passer, mine: dict, members, group, base_cap, now) -> dict | None:
    """N-255: the origin a passer takes from first — of another lobby, free of the pair rule, the most points for the
    least conversion — with the points to convert to beat it (0: it already does); None when it beats none."""
    if mine.get("crew") is None or mine.get("points") is None:
        return None
    best = None
    for m in members:
        if m.group != group or m.role != PiracyMember.ORIGIN or m.game_account.account_id == passer.account_id:
            continue
        if pair_release(month, passer, m.game_account, now):
            continue
        theirs = strength(m.game_account)
        if theirs.get("crew") is None:
            continue
        target = {"crew": at_raid(month, theirs["crew"], theirs.get("points")), "points": theirs.get("points")}
        convert = _convert_to_win(month, mine, target, base_cap)
        if convert is None:
            continue
        gain = max(int(theirs.get("points") or 0), month.min_points) - convert
        if best is None or gain > best["gain"]:
            best = {"ga": m.game_account, "convert": convert, "gain": gain}
    return best


def champion_takes(month) -> dict:
    """N-217: capture points each champion took from the plan's raids this month (raids sent or done)."""
    out = defaultdict(int)
    for raider_id, points in month.routes.filter(status__in=(PiracyRoute.SENT, PiracyRoute.DONE), target__isnull=False) \
            .values_list("raider_id", "target_points"):
        out[raider_id] += int(points or 0)
    return out


def _leave_to_other_champion(month, game_account, target, theirs, now_members, group) -> bool:
    """N-217: with both champions able to take this account, the one far ahead (more than BALANCE_SHARE of its take,
    at least min_points) leaves it to the other — within the margin it is free, they need not be alike."""
    others = [m.game_account for m in now_members if m.group == group and m.role == PiracyMember.CHAMPION
              and m.game_account_id != game_account.pk and m.game_account.account_id != target.account_id]
    if not others:
        return False
    takes = champion_takes(month)
    mine = takes.get(game_account.pk, 0)
    for other in others:
        if not sure_win(strength(other)["crew"], theirs["crew"], month.min_win_percent):
            continue
        if mine - takes.get(other.pk, 0) > max(month.min_points, BALANCE_SHARE * mine):
            return True
    return False


def _outside_route(month, member, game_account, mine, now, week, role=None) -> dict | None:
    """A target added by hand, raided by a champion (the strongest crew) — not twice by the same one in 7 days."""
    if (role or member.role) != PiracyMember.CHAMPION:
        return None
    recent = set(PiracyRoute._base_manager.filter(raider=game_account, created_at__gte=now - timedelta(days=month.pair_days or PAIR_DAYS))
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


# ── the page (N-255): the chain board ───────────────────────────────────────────────────────────────────────────────

LOBBY_COLORS = ["#4f86b9", "#c08a3e", "#4d9a6a", "#8a6bb5", "#b5523b", "#5c8f8a"]


def _converting_until(game_account) -> int:
    from apps.jobs.models import Job

    job = Job._base_manager.filter(game_account=game_account, action_code=PIRATE_ACTION).order_by("-created_at").first()
    return int(_job_inputs(job).get("_plan_convert_until") or 0) if job else 0


def board(month, rows_by_ga: dict, now: datetime | None = None) -> dict:
    """Everything the piracy page shows of the running plan: exposed points, the two champions side by side, the chain
    (origins -> passers -> champions) with what each account does next, and the feed of raids."""
    now = now or timezone.now()
    members = members_now(month, now)
    lobbies = sorted({m.game_account.account_id for m in members},
                     key=lambda a: -sum(1 for m in members if m.game_account.account_id == a))
    color = {lobby: LOBBY_COLORS[i % len(LOBBY_COLORS)] for i, lobby in enumerate(lobbies)}
    routes = list(month.routes.select_related("raider", "target", "outside").order_by("-created_at")[:40])
    active = [r for r in routes if r.status in (PiracyRoute.PLANNED, PiracyRoute.SENT)]
    by_target = {r.target_id: r for r in active if r.target_id}
    by_raider = {r.raider_id: r for r in active}
    takes = champion_takes(month)
    scale = max(1, month.min_points * 2)
    limits = budgets(month, {k: v.get("crew") for k, v in rows_by_ga.items()},
                     {k: v.get("capture_points") for k, v in rows_by_ga.items() if v.get("capture_points") is not None})

    def read(ga):
        row = rows_by_ga.get(ga.pk) or {}
        return {"crew": row.get("crew"), "points": row.get("capture_points")}

    def node(m):
        ga, r = m.game_account, read(m.game_account)
        points = int(r["points"] or 0)
        out = {"ga": ga, "role": m.role, "points": points, "crew": r["crew"], "color": color[ga.account_id],
               "lobby": ga.account.label if ga.account_id else "", "fill": min(100, round(points * 100 / scale)),
               "tone": "mut", "text": ""}
        route_in, route_out = by_target.get(ga.pk), by_raider.get(ga.pk)
        if m.role == PiracyMember.CHAMPION:
            if route_out and route_out.status == PiracyRoute.SENT:
                out.update(tone="warn", text=f"assalto a caminho · {route_out.target_name} ({route_out.target_points or 0:,})".replace(",", "."))
            elif route_out:
                out.update(tone="ok", text=f"rouba {route_out.target_name} agora")
            else:
                out.update(tone="ok", text="livre")
        elif m.role == PiracyMember.PASSER:
            until = _converting_until(ga)
            if until > now.timestamp():
                ready = timezone.localtime(datetime.fromtimestamp(until, tz=datetime_timezone.utc))
                out.update(tone="warn", text=f"convertendo · pronta {ready:%H:%M}")
            elif route_out:
                out.update(tone="warn" if route_out.status == PiracyRoute.SENT else "ok",
                           text=("a caminho · " if route_out.status == PiracyRoute.SENT else "rouba ") + route_out.target_name)
            else:
                best = _best_target(month, ga, r, members, m.group, (limits.get(ga.pk) or {}).get("cap_base"), now)
                if best is None:
                    out.update(tone="bad", text="não alcança as origens")
                elif best["convert"]:
                    out.update(tone="warn", text=f"converte {best['convert']:,} para vencer {best['ga'].name}".replace(",", "."))
                else:
                    out.update(tone="ok", text=f"já vence {best['ga'].name} sem converter")
        else:
            if route_in and route_in.status == PiracyRoute.SENT:
                out.update(tone="warn", text=f"{route_in.raider.name} a caminho")
            elif route_in:
                out.update(tone="ok", text=f"vai para {route_in.raider.name}")
            elif points >= month.min_points:
                out.update(tone="warn", text=(rows_by_ga.get(ga.pk) or {}).get("why") or "aguardando")
            else:
                out.update(tone="mut", text="acumulando")
        return out

    nodes = [node(m) for m in members]
    origins = sorted([n for n in nodes if n["role"] == PiracyMember.ORIGIN], key=lambda n: -n["points"])
    passers = sorted([n for n in nodes if n["role"] == PiracyMember.PASSER], key=lambda n: -n["points"])
    champions = [n for n in nodes if n["role"] == PiracyMember.CHAMPION]
    for n in champions:
        n["took"] = takes.get(n["ga"].pk, 0)
    exposed = origins + passers
    total = sum(n["points"] for n in exposed)
    duel = None
    if len(champions) == 2:
        a, b = sorted(champions, key=lambda n: -n["took"])
        both = a["took"] + b["took"]
        ahead = a["took"] - b["took"] > max(month.min_points, BALANCE_SHARE * a["took"])
        duel = {"left": a, "right": b, "left_pct": round(a["took"] * 100 / both) if both else 50,
                "text": (f"{a['ga'].name} à frente · alvos em comum vão para {b['ga'].name}" if ahead
                         else "dentro da margem · pega quem estiver pronta")}
    feed = []
    for r in routes:
        if r.status == PiracyRoute.SKIPPED:
            continue
        feed.append({"route": r, "kind": r.status})
        if len(feed) >= 8:
            break
    return {"origins": origins, "passers": passers, "champions": champions, "duel": duel, "feed": feed,
            "exposed": total, "exposed_count": sum(1 for n in exposed if n["points"]),
            "exposed_parts": [{"name": n["ga"].name, "pct": round(n["points"] * 100 / total), "color": n["color"]}
                              for n in sorted(exposed, key=lambda n: -n["points"]) if total and n["points"]],
            "legend": [{"label": next(m.game_account.account.label for m in members if m.game_account.account_id == lobby),
                        "color": color[lobby]} for lobby in lobbies],
            "bonus_mark": min(100, round(CREW_BONUS_FULL * 100 / scale)), "points_mark": min(100, round(month.min_points * 100 / scale)),
            "day": max(1, (now - month.starts_at).days + 1) if now > month.starts_at else 0,
            "days": max(1, (month.ends_at - month.starts_at).days)}
