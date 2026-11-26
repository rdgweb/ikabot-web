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

from types import SimpleNamespace

from . import flow
from . import tree as daytree
from .models import PiracyChampion, PiracyMember, PiracyMonth, PiracyRoute, PiracyTarget

PIRATE_ACTION = 17
CHAMPIONS = 2
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
    # N-263: only a job that is running or will run (an old "finished"/"error" one counted: Blahh, coisa, Icarus and
    # PacMan, whose Pirata Automático was cancelled on 23/11, joined the plan by it)
    with_job = set(Job.objects.filter(game_account__in=accounts, action_code=PIRATE_ACTION,
                                      status__in=("queued", "running", "scheduled"))
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
        # N-263: the numbers as the job last read them, and when; a raid of the plan since then took all the points
        read_at = _read_at(inputs)
        capture = inputs.get("last_capture_points")
        crew = inputs.get("last_complete_crew_points", inputs.get("last_crew_points"))
        robbed = robbed_after(ga, read_at) if capture else None
        if robbed:
            crew = int(crew) - crew_bonus(int(capture)) if crew is not None else None
            capture = 0
        above = next((e for e in highscore.get("entries") or []
                      if own.get("place") and int(e.get("place") or 0) == int(own.get("place")) - 1), None)
        rows.append({
            "gap_up": (int(above.get("score") or 0) - int(own.get("score") or 0)) if above else None,
            "above_name": (above or {}).get("player_name") or "",
            "ga": ga, "lobby": ga.account.label, "fortress": fortress, "room": town_room(snap.cities if snap else None),
            "place": own.get("place"), "score": own.get("score"),
            "capture_points": capture, "crew": crew, "robbed_at": robbed,
            "read_at": datetime.fromtimestamp(read_at, tz=datetime_timezone.utc) if read_at else None,
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
START_DELAY = timedelta(minutes=15)   # the plan starts this long after it is drawn
TURN_MARGIN = timedelta(minutes=30)   # the last raid before a ranking turn is drawn at least this long before it
PAIR_DAYS = 3                # the same raider takes from the same account once in this many days (N-217: was 7)
CREW_BONUS_MAX = 1400        # N-218: crew strength the held capture points add (the game's completeCrewPoints has it)
CREW_BONUS_FULL = 7000       # points that give the bonus (Rodrigo; measured: all or nothing)
ROTATE_DAYS = 1              # N-263: the roles follow the day (see flow) — kept for the old plans' field
TURN_HOUR = 14               # N-263: the plan's day turns at this hour of the GAME's clock (2 h before 16 h there)
DIRECT_POINTS = 20000        # N-263: a champion takes an origin directly only from this many points
SHARE_JITTER_DOWN, SHARE_JITTER_UP = 2, 0   # N-263: an origin's share is drawn this much below/above the plan's (3% -> 1-3%, as agreed)
BALANCE_SHARE = 0.25         # N-217: a champion this far ahead of the other (share of its own take, at least
                             # min_points) leaves a target both can take to the other one
ROUTE_HOURS = 6              # a raid decided and not sent in this time is dropped (the next free ship gets a new one)
SENT_HOURS = 6               # a raid sent and never answered in this time is closed (the agent lost it)


def current_month(server_id: str):
    return PiracyMonth.objects.filter(server_id=server_id, status=PiracyMonth.OPEN).order_by("-starts_at").first()


def rotation_index(month, now: datetime | None = None) -> int:
    """N-263: the plan's day number (0 the day it started) — the roles change every day."""
    return flow.day_number(month, now)


def next_rotation(month, now: datetime | None = None):
    """N-263: when the day turns (and the passers of the day change)."""
    return flow.turn_end(month, now)


def members_now(month, now: datetime | None = None) -> list:
    """N-263: the month's accounts with their role in the day's tree: the champions, the ones that carry others today
    (passers) and the rest (origins) — see tree."""
    return members_of(daytree.build(month, now))


def members_of(t) -> list:
    return [SimpleNamespace(game_account=n.ga, game_account_id=n.pk, group=1, role=n.role) for n in t.nodes.values()]


def at_raid(month, crew: int | None, points: int | None) -> int | None:
    """N-255: an account's strength when it is raided — it holds min_points or more then (the bonus of those)."""
    if crew is None:
        return None
    held = int(points or 0)
    return crew - crew_bonus(held) + crew_bonus(max(held, month.min_points))


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


@transaction.atomic
def plan_month(organization, server_id: str, *, champions: list | None = None, now: datetime | None = None,
               rng: random.Random | None = None, share_percent: int = 3, share_every_hours: int = 24,
               min_win_percent: int = 95, min_points: int = 10000, pair_days: int = 3, rotate_days: int | None = None,
               turn_hour: int | None = None, direct_points: int | None = None):
    """Close the open month of that server and start a new one: the two champions and every other account as an
    origin — the passers of each day come from the 3-day agenda (flow)."""
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
                                       pair_days=max(1, min(30, int(pair_days or 3))),
                                       turn_hour=TURN_HOUR if turn_hour is None else max(0, min(23, int(turn_hour))),
                                       direct_points=DIRECT_POINTS if direct_points is None else max(0, int(direct_points)))
    for champion in chosen:
        PiracyChampion.objects.create(month=month, game_account=champion, group=1)
        PiracyMember.objects.create(month=month, game_account=champion, role=PiracyMember.CHAMPION, group=1)
    for ga in accounts:
        if ga not in chosen:
            PiracyMember.objects.create(month=month, game_account=ga, role=PiracyMember.ORIGIN, group=1)
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
    # a raid "on its way" for hours was lost by the agent: it would show forever as on its way, keep its target taken
    # from everyone and count points the champion never got (HAVIT -> Executor, 23/11)
    skipped += PiracyRoute._base_manager.filter(status=PiracyRoute.SENT, sent_at__lt=now - timedelta(hours=SENT_HOURS)).update(
        status=PiracyRoute.SKIPPED, done_at=now, message="sem retorno do agente; fechado sozinho")
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
                           min_points=month.min_points, pair_days=month.pair_days, rotate_days=month.rotate_days,
                           turn_hour=month.turn_hour, direct_points=month.direct_points)
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

def _read_at(inputs: dict) -> int:
    return int(inputs.get("last_state_updated_at") or inputs.get("piracy_highscore_updated_at") or 0)


def robbed_after(game_account, read_at: int):
    """N-263: when one of the plan's raids took this account's points after the job last read them (None: not since):
    a raid reported won, or one on its way that has already landed (the raider reports only when its ship is back).
    The winner takes all the capture points at once, so until the next reading it holds none."""
    from django.db.models import Q

    if not read_at:
        return None
    since = datetime.fromtimestamp(read_at, tz=datetime_timezone.utc)
    landed = (Q(status=PiracyRoute.DONE, done_at__gt=since)
              | Q(status=PiracyRoute.SENT, arrives_at__gt=since, arrives_at__lte=timezone.now()))
    times = [done or arrives for done, arrives in PiracyRoute._base_manager.filter(target=game_account).filter(landed)
             .values_list("done_at", "arrives_at")]
    return max(times) if times else None


def strength(game_account) -> dict:
    """What the Pirate Fortress job last read of this account: capture points and crew strength (None when unknown),
    and when it read them. N-263: a raid of the plan that took its points since then leaves it with none."""
    from apps.jobs.models import Job

    job = (Job._base_manager.filter(game_account=game_account, action_code=PIRATE_ACTION).order_by("-created_at").first())
    inputs = _job_inputs(job) if job else {}
    crew = inputs.get("last_complete_crew_points", inputs.get("last_crew_points"))
    points = inputs.get("last_capture_points")
    crew = int(crew) if crew is not None else None
    points = int(points) if points is not None else None
    read_at = _read_at(inputs)
    if points and robbed_after(game_account, read_at):
        crew = crew - crew_bonus(points) if crew is not None else None
        points = 0
    return {"crew": crew, "points": points, "read_at": read_at}


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
    fresh_read = {game_account.pk: {**strength(game_account), **fresh}} if fresh else {}
    t = daytree.build(month, now, read=fresh_read)
    node = t.node(game_account)
    role = node.role if node is not None else member.role
    hold = role != PiracyMember.CHAMPION
    out = {"member": member, "month": month, "members": members_of(t), "tree": t, "role": role, "hold": hold,
           "final": False, "convert_all": not hold, "share_percent": month.share_percent if hold else 0,
           "share_jitter": [SHARE_JITTER_DOWN, SHARE_JITTER_UP], "preconvert_points": 0, "preconvert_target": "",
           "convert_total": None, "convert_by": 0, "convert_target": None,
           "share_every_hours": month.share_every_hours, "crew_cap": None, "crew_floor": None, "base_cap": None,
           "bonus_max": CREW_BONUS_MAX, "bonus_full": CREW_BONUS_FULL}
    if hold and node is not None:
        # N-263: it stays beatable by whoever takes it today (cap); it converts nothing of what it collects for its
        # champion; one that can reach an account no chain reaches converts, a part at each mission, to the strength
        # that does; the others keep the plan's 1-3% a day, a part at each mission
        cap = daytree.cap_of(month, node)
        out.update(crew_cap=cap, base_cap=cap - CREW_BONUS_MAX if cap is not None else None)
        builder = t.builders.get(node.pk)
        if node.children and node.collect_from is not None and now >= node.collect_from:
            out.update(share_percent=0, convert_total=0, preconvert_target="recolhendo para a campeã")
        elif builder is not None:
            out.update(share_percent=0, convert_total=builder["convert"], convert_target=builder["target"],
                       convert_by=int(turn_end(month, now).timestamp()),
                       preconvert_target=f"alcançar {builder['for'].name} (força {builder['target']})")
        # in the final phase of the ranking (the points count for the position at the turn) nobody converts
        if _final_phase(game_account):
            out.update(share_percent=0, crew_floor=None, final=True, preconvert_points=0, convert_total=0)
    return out


def turn_end(month, now: datetime | None = None):
    return flow.turn_end(month, now)


def deliver_at(month, now: datetime | None = None):
    return flow.deliver_at(month, now)


def raid_seconds(raider) -> int:
    return flow.raid_seconds(raider)


def drop_stale(month, t) -> int:
    """N-263: raids decided that the day's tree no longer has (the roles changed, a pair got locked) are dropped — one
    left open held its target from everyone for hours (Carcereiro -> NexX, 26/11)."""
    edges = {(n.parent.pk, n.pk) for n in t.nodes.values() if n.parent is not None}
    stale = [r.pk for r in month.routes.filter(status=PiracyRoute.PLANNED, target__isnull=False)
             if (r.raider_id, r.target_id) not in edges]
    if stale:
        PiracyRoute._base_manager.filter(pk__in=stale).update(status=PiracyRoute.SKIPPED, message="a corrente mudou")
    return len(stale)


def _taken(month) -> set:
    """Accounts with a raid of the plan decided or on its way (one raid on an account at a time)."""
    return set(PiracyRoute._base_manager.filter(month=month, status__in=(PiracyRoute.PLANNED, PiracyRoute.SENT))
               .values_list("target_id", flat=True))


def next_for(game_account, now: datetime | None = None, fresh: dict | None = None) -> dict:
    """What the Pirate Fortress job of this account does for the plan now (N-263, see tree):
    {"route": {...} | None, "hold": bool, "convert_all": bool, "share_percent": int, "share_every_hours": int,
    "route_at": when to come back for a raid planned later, "convert_total"/"convert_by": the crew plan}.
    An account takes the ones hanging on it in the day's tree when their time comes (a champion the ones straight
    under it at once and its carriers at the delivery; a carrier its own ones just before it is taken). Champions
    convert all their points; the others hold, converting a part at each mission."""
    now = now or timezone.now()
    expire(now)
    fresh = {k: int(v) for k, v in (fresh or {}).items() if v is not None}
    plan = plan_for(game_account, now, fresh)
    if plan is None:
        return {"route": None, "hold": False}
    member, month, role, hold = plan["member"], plan["month"], plan["role"], plan["hold"]
    extra = {k: plan.get(k) for k in ("convert_all", "share_percent", "share_every_hours", "crew_cap", "crew_floor",
                                      "base_cap", "bonus_max", "bonus_full", "share_jitter", "preconvert_points",
                                      "preconvert_target", "convert_total", "convert_by", "convert_target")}
    if plan.get("waiting"):
        return {"route": None, "hold": True, **extra, "starts_at": int(month.starts_at.timestamp())}
    t = plan["tree"]
    drop_stale(month, t)
    open_route = (PiracyRoute._base_manager.filter(raider=game_account, month=month, status=PiracyRoute.PLANNED, due_until__gt=now)
                  .select_related("raider__account", "target__account", "outside").order_by("due_from").first())
    if open_route is not None:
        if open_route.due_from > now:                       # drawn for later: the ship must be free then
            return {"route": None, "hold": hold, **extra, "route_at": int(open_route.due_from.timestamp())}
        info = _target_info(open_route.target) if open_route.target_id else _outside_info(open_route.outside)
        if info:
            return {"route": {**_payload(open_route, info), "convert_points": 0}, "hold": hold, **extra}
    if role == PiracyMember.ORIGIN:
        return {"route": None, "hold": hold, **extra}
    # N-218: never at or right before the ranking turn: every account's capture points go back to 0 then
    if month.turn_at and month.turn_at - TURN_MARGIN <= now < month.turn_at:
        return {"route": None, "hold": hold, **extra}
    mine = {**strength(game_account), **fresh}
    week = max(1, int((now - month.starts_at).total_seconds() // (7 * 86400)) + 1)
    taken = _taken(month)
    ready, next_at = daytree.due(t, game_account, now)
    for child in ready:
        if child.points < month.min_points or child.pk in taken or not _target_info(child.ga):
            continue
        until = now + timedelta(hours=ROUTE_HOURS)
        if month.turn_at and now < month.turn_at:
            until = min(until, month.turn_at)
        route = PiracyRoute.objects.create(month=month, group=member.group, week=week, stage=child.depth,
                                           raider=game_account, target=child.ga, target_points=child.points,
                                           raider_crew=mine.get("crew"), target_crew=child.at_raid,
                                           due_from=now, due_until=until)
        route = PiracyRoute._base_manager.select_related("raider", "target").get(pk=route.pk)
        return {"route": {**_payload(route, _target_info(child.ga)), "convert_points": 0}, "hold": hold, **extra}
    outside = _outside_route(month, member, game_account, mine, now, week, role)
    if outside is None and next_at is not None:
        # the ship comes back for its raid at the planned time (a mission that ends after it waits)
        return {"route": None, "hold": hold, **extra, "route_at": int(next_at.timestamp())}
    return {"route": outside, "hold": hold, **extra}


def pair_release(month, raider, target, now: datetime | None = None):
    """N-255: when this raider may take from this account again (None: it may now)."""
    now = now or timezone.now()
    last = (PiracyRoute._base_manager.filter(raider=raider, target=target, created_at__gte=now - timedelta(days=month.pair_days or PAIR_DAYS))
            .exclude(status=PiracyRoute.SKIPPED).order_by("-created_at").first())
    return last.created_at + timedelta(days=month.pair_days or PAIR_DAYS) if last else None


def champion_takes(month) -> dict:
    """N-217: capture points each champion took from the plan's raids this month (raids sent or done)."""
    out = defaultdict(int)
    for raider_id, points in month.routes.filter(status__in=(PiracyRoute.SENT, PiracyRoute.DONE), target__isnull=False) \
            .values_list("raider_id", "target_points"):
        out[raider_id] += int(points or 0)
    return out


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


def report(route, status: str, message: str = "", seconds: int | None = None) -> None:
    """The agent says how the raid went: sent (on its way; N-263: with the seconds until it lands), done (won: the
    points came), failed (lost or not sent)."""
    now = timezone.now()
    if status == PiracyRoute.SENT:
        route.status, route.sent_at = status, now
        route.arrives_at = now + timedelta(seconds=int(seconds)) if seconds and int(seconds) > 0 else None
        # a ship carries one raid: an older one still "on its way" for this raider was lost
        PiracyRoute._base_manager.filter(raider_id=route.raider_id, status=PiracyRoute.SENT).exclude(pk=route.pk).update(
            status=PiracyRoute.SKIPPED, done_at=now, message="sem retorno do agente; outro assalto saiu")
    elif status in (PiracyRoute.DONE, PiracyRoute.FAILED):
        route.status, route.done_at = status, now
    else:
        return
    route.message = (message or "")[:255]
    route.save(update_fields=["status", "sent_at", "arrives_at", "done_at", "message", "updated_at"])


# ── the page (N-255): the chain board ───────────────────────────────────────────────────────────────────────────────

LOBBY_COLORS = ["#4f86b9", "#c08a3e", "#4d9a6a", "#8a6bb5", "#b5523b", "#5c8f8a"]


def _converting_until(game_account) -> int:
    from apps.jobs.models import Job

    job = Job._base_manager.filter(game_account=game_account, action_code=PIRATE_ACTION).order_by("-created_at").first()
    return int(_job_inputs(job).get("_plan_convert_until") or 0) if job else 0


def _hm(when, now) -> str:
    """N-263: a time on one line — "14:05" today, "27 14:05" another day."""
    if not when:
        return ""
    local, today = timezone.localtime(when), timezone.localtime(now)
    return f"{local:%H:%M}" if local.date() == today.date() else f"{local:%d %H:%M}"


def _ago(when, now) -> str:
    """N-263: "3h 20m" / "2d 4h" since then."""
    seconds = max(0, int((now - when).total_seconds()))
    days, rest = divmod(seconds, 86400)
    return f"{days}d {rest // 3600}h" if days else f"{rest // 3600}h {(rest % 3600) // 60:02d}m"


def _emptied(month) -> dict:
    """N-263: the last time each account of the plan gave its points away (a raid of the plan that won; the ones
    cleared by hand on 26/11 keep "era done" in their message)."""
    from django.db.models import Q

    out = {}
    for target_id, done_at, created_at in (month.routes.filter(target__isnull=False)
                                           .filter(Q(status=PiracyRoute.DONE) | Q(status=PiracyRoute.SKIPPED, message__contains="era done"))
                                           .values_list("target_id", "done_at", "created_at")):
        when = done_at or created_at
        if target_id not in out or when > out[target_id]:
            out[target_id] = when
    return out


def board(month, rows_by_ga: dict, now: datetime | None = None) -> dict:
    """Everything the piracy page shows of the running plan: exposed points, the two champions side by side, the
    day's tree (who takes whom, through how many hops, at what time, and what each account converts), the chain
    columns by role, and the feed of raids."""
    now = now or timezone.now()
    read_now = {pk: {"crew": row.get("crew"), "points": row.get("capture_points")} for pk, row in rows_by_ga.items()
                if row.get("crew") is not None}
    t = daytree.build(month, now, read=read_now)
    drop_stale(month, t)
    members = members_of(t)
    lobbies = sorted({m.game_account.account_id for m in members},
                     key=lambda a: -sum(1 for m in members if m.game_account.account_id == a))
    color = {lobby: LOBBY_COLORS[i % len(LOBBY_COLORS)] for i, lobby in enumerate(lobbies)}
    routes = list(month.routes.select_related("raider", "target", "outside").order_by("-created_at")[:40])
    active = [r for r in routes if r.status in (PiracyRoute.PLANNED, PiracyRoute.SENT)]
    by_target = {r.target_id: r for r in active if r.target_id}
    by_raider = {r.raider_id: r for r in active}
    takes = champion_takes(month)
    emptied = _emptied(month)
    scale = max(1, month.min_points * 2)

    def lands(route) -> str:
        return f" · chega {_hm(route.arrives_at, now)}" if route.arrives_at else ""

    def node(m):
        ga, n = m.game_account, t.node(m.game_account)
        row = rows_by_ga.get(ga.pk) or {}
        points = int(row.get("capture_points") or 0)
        out = {"ga": ga, "role": m.role, "points": points, "crew": row.get("crew"), "color": color[ga.account_id],
               "lobby": ga.account.label if ga.account_id else "", "fill": min(100, round(points * 100 / scale)),
               "tone": "mut", "text": "", "read": _hm(row.get("read_at"), now),
               "stale": bool(row.get("read_at") and now - row["read_at"] > timedelta(hours=2)),
               "robbed": _hm(row.get("robbed_at"), now), "since": ""}
        if m.role != PiracyMember.CHAMPION and points:
            out["since"] = _ago(emptied.get(ga.pk) or month.starts_at, now)
        route_in, route_out = by_target.get(ga.pk), by_raider.get(ga.pk)
        until = _converting_until(ga)
        if route_out is not None:
            out.update(tone="warn" if route_out.status == PiracyRoute.SENT else "ok",
                       text=("assalto em " if route_out.status == PiracyRoute.SENT else "rouba ") + route_out.target_name + lands(route_out))
        elif route_in is not None:
            out.update(tone="warn", text=f"{route_in.raider.name} a caminho" + lands(route_in))
        elif until > now.timestamp():
            out.update(tone="warn", text=f"convertendo · pronta {_hm(datetime.fromtimestamp(until, tz=datetime_timezone.utc), now)}")
        elif n is not None and n.children and not n.champion:
            out.update(tone="ok", text=f"recolhe {len(n.children)} às {_hm(n.collect_from, now)} · vai para {n.parent.ga.name} às {_hm(n.taken_at, now)}")
        elif n is not None and n.champion:
            out.update(tone="ok", text=f"recebe {len(n.children)} conta{'s' if len(n.children) != 1 else ''} hoje")
        elif n is not None and n.parent is not None:
            out.update(tone="ok" if points >= month.min_points else "mut",
                       text=f"vai para {n.parent.ga.name} às {_hm(n.taken_at, now)}" if points >= month.min_points
                       else f"acumulando · vai para {n.parent.ga.name}")
        else:
            out.update(tone="bad" if points >= month.min_points else "mut", text="sem caminho hoje")
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
                "text": (f"{a['ga'].name} à frente · a corrente manda mais para {b['ga'].name}" if ahead
                         else "dentro da margem")}
    feed = []
    for r in routes:
        if r.status == PiracyRoute.SKIPPED:
            continue
        feed.append({"route": r, "kind": r.status, "lands": _hm(r.arrives_at, now) if r.arrives_at else ""})
        if len(feed) >= 8:
            break
    champion_ids = {n["ga"].pk for n in champions}
    delivered = sum(int(points or 0) for points in month.routes.filter(
        raider_id__in=champion_ids, target__isnull=False, status__in=(PiracyRoute.SENT, PiracyRoute.DONE),
        sent_at__gte=now - timedelta(hours=24)).values_list("target_points", flat=True))
    waiting = [n for n in exposed if n["since"] and n["points"] >= month.min_points]
    oldest = min(waiting, key=lambda n: emptied.get(n["ga"].pk) or month.starts_at, default=None)
    return {"origins": origins, "passers": passers, "champions": champions, "duel": duel, "feed": feed,
            "exposed": total, "exposed_count": sum(1 for n in exposed if n["points"]),
            "exposed_parts": [{"name": n["ga"].name, "pct": round(n["points"] * 100 / total), "color": n["color"]}
                              for n in sorted(exposed, key=lambda n: -n["points"]) if total and n["points"]],
            "legend": [{"label": next(m.game_account.account.label for m in members if m.game_account.account_id == lobby),
                        "color": color[lobby]} for lobby in lobbies],
            "bonus_mark": min(100, round(CREW_BONUS_FULL * 100 / scale)), "points_mark": min(100, round(month.min_points * 100 / scale)),
            "day": max(1, (now - month.starts_at).days + 1) if now > month.starts_at else 0,
            "days": max(1, (month.ends_at - month.starts_at).days),
            "tree": tree_rows(month, t, color, now), "delivered": delivered, "oldest": oldest,
            "deliver_at": _hm(t.deliver, now)}


def tree_rows(month, t, color, now) -> list[dict]:
    """N-263: the day's tree as rows, from each champion down (depth gives the indent): who takes it, when the raid
    goes out and lands, what it converts; then the accounts no chain reaches."""
    rows = []

    def walk(n, depth):
        builder = t.builders.get(n.pk)
        rows.append({"ga": n.ga, "depth": depth, "champion": n.champion, "points": n.points, "power": n.power,
                     "color": color.get(n.ga.account_id, "#999"), "why": n.why, "carries": len(n.children),
                     "send": _hm(n.send_at, now) if n.send_at else "", "lands": _hm(n.taken_at, now) if n.taken_at else "",
                     "collect": _hm(n.collect_from, now) if n.collect_from else "",
                     "below": n.points < month.min_points and not n.champion,
                     "builder": builder})
        for child in sorted(n.children, key=lambda c: (-len(c.children), -c.points)):
            walk(child, depth + 1)

    for champion in t.champions:
        walk(champion, 0)
    for n in t.unreached:
        rows.append({"ga": n.ga, "depth": 0, "champion": False, "points": n.points, "power": n.power,
                     "color": color.get(n.ga.account_id, "#999"), "why": n.why or "sem caminho hoje", "carries": 0,
                     "send": "", "lands": "", "collect": "", "below": n.points < month.min_points, "builder": None,
                     "unreached": True})
    return rows
