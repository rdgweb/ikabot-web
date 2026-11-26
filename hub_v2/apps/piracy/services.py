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

from . import flow
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
    """The month's accounts with their role now: the champions, the passers of the day (the 3-day agenda) and the
    origins — see flow."""
    return flow.members_today(month, now)


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
    now_members = members_now(month, now)
    role = next((m.role for m in now_members if m.game_account_id == game_account.pk), member.role)
    hold = role != PiracyMember.CHAMPION
    out = {"member": member, "month": month, "members": now_members, "role": role, "hold": hold, "final": False,
           "convert_all": not hold, "share_percent": month.share_percent if role == PiracyMember.ORIGIN else 0,
           "share_jitter": [SHARE_JITTER_DOWN, SHARE_JITTER_UP], "preconvert_points": 0, "preconvert_target": "",
           "convert_total": None, "convert_by": 0, "convert_target": None,
           "share_every_hours": month.share_every_hours, "crew_cap": None, "crew_floor": None, "base_cap": None,
           "bonus_max": CREW_BONUS_MAX, "bonus_full": CREW_BONUS_FULL}
    if hold:
        read = {m.game_account_id: strength(m.game_account) for m in now_members if m.group == member.group}
        if fresh:
            read[game_account.pk] = {**read.get(game_account.pk, {}), **fresh}
        mine_budget = budgets(month, {k: v["crew"] for k, v in read.items()},
                              {k: v["points"] for k, v in read.items() if v["points"] is not None}).get(game_account.pk) or {}
        out.update(crew_cap=mine_budget.get("cap"), crew_floor=None, base_cap=mine_budget.get("cap_base"))
        # N-263: the crew plan — the agenda says who passes and when; until that day the account converts, a part at
        # each mission, what makes its strength beat the origins it collects then (convert_total by convert_by); it
        # stops there or at the crew its own raiders still beat, and converts nothing of what it collects for its
        # champion. The others keep the plan's 1-3% a day, also a part at each mission.
        conv = conversion_plan(month, game_account, now_members, read.get(game_account.pk) or {},
                               out["base_cap"] if role == PiracyMember.ORIGIN else None, now)
        if conv is not None:
            out.update(share_percent=0, convert_total=conv["total"], convert_by=int(conv["by"].timestamp()),
                       convert_target=conv["target"], preconvert_target=conv["why"])
        # in the final phase of the ranking (the points count for the position at the turn) nobody converts
        if _final_phase(game_account):
            out.update(share_percent=0, crew_floor=None, final=True, preconvert_points=0, convert_total=0)
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


def passer_need(month, champion, passer, members, fresh: dict | None = None) -> dict:
    """N-263: the crew plan of one passer of the day — the strength to have at its raids on the origins of its
    champion's lobby (the ones the champion never takes directly), each holding min_points or more then (the bonus).
    Not "beat them all": the strength that brings the most points for the least conversion — an origin with a crew
    out of reach (ScorpiO 7.052 after converting by hand) does not raise the bar for the others. Never above what its
    champion still beats while it carries the points (cap).
    {"target", "now", "convert" (points to convert now; None: its points reach no origin), "cap", "sources" (the
    origins it beats at the target), "left_out" (the ones above it: another way or another day)}."""
    ratio = ratio_for(month.min_win_percent)
    mine = {**strength(passer), **(fresh or {})}
    champ = strength(champion)["crew"]
    cap = int(champ / ratio) if champ else None             # the strength it may have when its champion raids it
    origins = []
    for m in members:
        o = m.game_account
        if m.role != PiracyMember.ORIGIN or o.account_id != champion.account_id or o.account_id == passer.account_id:
            continue
        theirs = strength(o)
        if theirs["crew"] is None:
            continue
        need = int(at_raid(month, theirs["crew"], theirs["points"]) * ratio) + 1
        origins.append((need, max(int(theirs["points"] or 0), 0), o))
    origins.sort(key=lambda item: item[0])
    out = {"target": None, "now": mine.get("crew"), "convert": None, "cap": cap, "sources": [],
           "left_out": [o for _n, _p, o in origins]}
    if not origins:
        out["convert"] = 0
        return out
    points = int(mine.get("points") or 0)
    base = mine["crew"] - crew_bonus(points) if mine.get("crew") is not None else None
    cap_base = cap - CREW_BONUS_MAX if cap is not None else None   # it carries the collection when emptied
    best = None
    for need, _p, _o in origins:
        if cap is not None and need > cap:
            break
        if base is None:
            break
        convert = 0 if mine["crew"] >= need else convert_for(base, points, need, cap_base)
        if convert is None:
            continue
        gain = sum(p for n, p, _o in origins if n <= need) - convert
        if best is None or gain > best[0]:
            best = (gain, need, convert)
    if best is None:
        out["target"] = origins[0][0]                        # not even the weakest: shown as out of reach
        return out
    _gain, target, convert = best
    out.update(target=target, convert=convert, sources=[o for n, _p, o in origins if n <= target],
               left_out=[o for n, _p, o in origins if n > target])
    return out


def conversion_plan(month, game_account, members, mine: dict, base_cap, now: datetime | None = None) -> dict | None:
    """N-263: what this account converts until its next day as a passer (this block or the next) — None when it has
    none ahead (it keeps the plan's daily share). The agent converts it a part at each mission, so that the strength
    is there on that day: {"total": points still to convert (0: stop), "by": when it must be there, "target", "why"}.
    It stops at the strength or at the crew its own raiders still beat, and converts nothing while it collects (what
    it holds then is its champion's)."""
    now = now or timezone.now()
    rows = crew_plan(month, now)
    row = next((r for r in rows if r["passer"].pk == game_account.pk), None)
    if row is None or not row["target"]:
        return None
    out = {"total": 0, "by": row["by"], "target": row["target"], "why": f"as origens (força {row['target']})"}
    # still to be collected before its day (a passer of an earlier day takes it): it must stay beatable until then
    earlier = [r for r in rows if r["day"] < row["day"] and any(o.pk == game_account.pk for o in r["sources"])]
    if earlier:
        out["why"] = f"espera ser recolhida por {earlier[-1]['passer'].name}"
        return out
    crew, points = mine.get("crew"), int(mine.get("points") or 0)
    if crew is None or crew >= row["target"] or points <= 0:
        return out                                          # there: stop
    if row["day"] == 0 and now >= collect_from(month, game_account, members, now):
        return out                                          # collecting: what it holds is its champion's
    base = crew - crew_bonus(points)
    total = row["convert"] if row["convert"] else (row["target"] - crew) * 10   # unreachable today: what is missing
    if base_cap is not None:                                # as an origin, its own raiders must still beat it
        total = min(total, max(0, (base_cap - base) * 10))
    out["total"] = max(0, int(total)) // 10 * 10
    return out


def crew_plan(month, now: datetime | None = None) -> list[dict]:
    """N-263: the crew plan of the coming passer days (the rest of this block and the next one) — for each, the
    passer, the strength it needs that day, what it has and what it still converts (a part at each mission, to be
    there when the day begins: a conversion only counts when it is over)."""
    now = now or timezone.now()
    by_pk = {str(m.game_account_id): m.game_account for m in month.members.select_related("game_account__account")}
    rows, members_of = [], {}
    for slot in flow.upcoming(month, now):
        champion, passer = by_pk.get(slot["champion"]), by_pk.get(slot["passer"])
        if champion is None or passer is None:
            continue
        if slot["offset"] not in members_of:
            members_of[slot["offset"]] = flow.members_today(month, slot["start"] + timedelta(hours=1))
        members = members_of[slot["offset"]]
        need = passer_need(month, champion, passer, members)
        # the strength must be there when it starts collecting today, or when its day begins
        by = collect_from(month, passer, members, now) if slot["offset"] == 0 else slot["start"]
        rows.append({"day": slot["offset"], "date": slot["start"], "by": by, "champion": champion, "passer": passer, **need})
    return rows


def turn_end(month, now: datetime | None = None):
    return flow.turn_end(month, now)


def deliver_at(month, now: datetime | None = None):
    return flow.deliver_at(month, now)


def raid_seconds(raider) -> int:
    return flow.raid_seconds(raider)


def drop_stale(month, members) -> int:
    """N-263: raids decided before the day turned that today's roles no longer allow (a passer that became an origin
    keeps no raid on its old origins) are dropped — one left open held its target from everyone for hours
    (Carcereiro -> NexX, 26/11)."""
    level = {m.game_account_id: PiracyMember.LEVEL[m.role] for m in members}
    stale = [r.pk for r in month.routes.filter(status=PiracyRoute.PLANNED, target__isnull=False)
             if level.get(r.raider_id, -1) <= level.get(r.target_id, 99)]
    if stale:
        PiracyRoute._base_manager.filter(pk__in=stale).update(status=PiracyRoute.SKIPPED, message="os papéis trocaram")
    return len(stale)


def _taken(month) -> set:
    """Accounts with a raid of the plan decided or on its way (one raid on an account at a time)."""
    return set(PiracyRoute._base_manager.filter(month=month, status__in=(PiracyRoute.PLANNED, PiracyRoute.SENT))
               .values_list("target_id", flat=True))


def champion_may_take(month, champion, ga, now: datetime | None = None, theirs: dict | None = None) -> bool:
    """N-263: a champion takes an origin directly — another lobby, the pair free, a sure win, and not its passer later
    in the block (the pair would be locked on that day) unless that one already holds direct_points: then the points
    are not left exposed for days — the champion takes them and the agenda gives that day to another account."""
    now = now or timezone.now()
    if ga.account_id == champion.account_id or pair_release(month, champion, ga, now):
        return False
    theirs = theirs or strength(ga)
    if str(ga.pk) in flow.still_to_pass(month, champion, now) and (theirs["points"] or 0) < max(month.direct_points or 0, month.min_points):
        return False
    return sure_win(strength(champion)["crew"], theirs["crew"], month.min_win_percent)


def direct_from(month, origin, members, now: datetime | None = None) -> int:
    """N-263: from how many points a champion takes this origin directly: month.direct_points while a passer of the
    day can collect it, month.min_points when none can."""
    now = now or timezone.now()
    theirs = strength(origin)
    for m in members:
        p = m.game_account
        if m.role != PiracyMember.PASSER or p.account_id == origin.account_id or pair_release(month, p, origin, now):
            continue
        if _convert_to_win(month, strength(p), theirs, None) is not None:
            return max(month.direct_points or 0, month.min_points)
    return month.min_points


def collectable(month, passer, members, now: datetime | None = None, base_cap=None, mine: dict | None = None) -> list:
    """N-263: the origins the passer takes this day — another lobby, the pair free, nobody else on them, the points
    to take, a win with the strength it has — leaving to the champions the ones over direct_points they take
    directly. [(account, its strength, 0)], the most points first."""
    now = now or timezone.now()
    taken = _taken(month)
    mine = mine or strength(passer)
    champions = [m.game_account for m in members if m.role == PiracyMember.CHAMPION]
    out = []
    for m in members:
        ga = m.game_account
        if m.role != PiracyMember.ORIGIN or ga.account_id == passer.account_id or ga.pk in taken:
            continue
        theirs = strength(ga)
        points = theirs["points"] or 0
        if points < month.min_points or pair_release(month, passer, ga, now):
            continue
        if points >= (month.direct_points or 0) and any(champion_may_take(month, c, ga, now, theirs) for c in champions):
            continue
        # N-263: only the ones it already beats — its conversion is the crew plan's, in the morning of its day (a
        # conversion made at the raid takes hours and would hold the points past the delivery)
        if _convert_to_win(month, mine, theirs, base_cap) == 0:
            out.append((ga, theirs, 0))
    return sorted(out, key=lambda item: (-((item[1]["points"] or 0) - item[2]), item[2]))


def still_collecting(month, passer, members, now: datetime | None = None) -> bool:
    """N-263: the passer's own raid is on its way, or it still has an origin to take."""
    active = PiracyRoute._base_manager.filter(month=month, raider=passer,
                                              status__in=(PiracyRoute.PLANNED, PiracyRoute.SENT))
    return active.exists() or bool(collectable(month, passer, members, now))


def collect_count(month, passer, members, now: datetime | None = None) -> int:
    return len(collectable(month, passer, members, now))


def collect_from(month, passer, members, now: datetime | None = None):
    """N-263: from when the passer collects: as late as lets its raids, one after the other, come back before its
    champion's raid lands at deliver_at — so the points it carries are open to others for the shortest time."""
    count = max(1, collect_count(month, passer, members, now))
    return deliver_at(month, now) - timedelta(seconds=raid_seconds(passer) * count)


def empties_at(month, passer, members, now: datetime | None = None, champion=None):
    """N-263: from when the champion sends its raid on its passer of the day: at once when the collecting window began
    and the passer has nothing left to take; else so that the raid lands at deliver_at."""
    now = now or timezone.now()
    if now >= collect_from(month, passer, members, now) and not still_collecting(month, passer, members, now):
        return now
    lands = deliver_at(month, now) - timedelta(seconds=raid_seconds(champion) // 2)
    return max(now, lands)


def next_for(game_account, now: datetime | None = None, fresh: dict | None = None) -> dict:
    """What the Pirate Fortress job of this account does for the plan now (N-263, see flow):
    {"route": {...} | None, "hold": bool, "convert_all": bool, "share_percent": int, "share_every_hours": int,
    "route_at": when to come back for a raid planned later}.
    A champion takes, as soon as possible, the origins over direct_points it may take directly, and its passer of the
    day at the delivery; a passer takes, in its window before the delivery, the origins it collects; an origin keeps
    its points. Champions convert all their points; the others hold, converting a share now and then."""
    now = now or timezone.now()
    expire(now)
    fresh = {k: int(v) for k, v in (fresh or {}).items() if v is not None}
    plan = plan_for(game_account, now, fresh)
    if plan is None:
        return {"route": None, "hold": False}
    member, month, now_members, role, hold = plan["member"], plan["month"], plan["members"], plan["role"], plan["hold"]
    flow.join_new(month)                                # N-263: an account that got its fortress and job joins
    extra = {k: plan.get(k) for k in ("convert_all", "share_percent", "share_every_hours", "crew_cap", "crew_floor",
                                      "base_cap", "bonus_max", "bonus_full", "share_jitter", "preconvert_points",
                                      "preconvert_target", "convert_total", "convert_by", "convert_target")}
    if plan.get("waiting"):
        return {"route": None, "hold": True, **extra, "starts_at": int(month.starts_at.timestamp())}
    drop_stale(month, now_members)
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
    # N-218: never at or right before the ranking turn: every account's capture points go back to 0 then
    if month.turn_at and month.turn_at - TURN_MARGIN <= now < month.turn_at:
        return {"route": None, "hold": hold, **extra}
    mine = {**strength(game_account), **fresh}
    week = max(1, int((now - month.starts_at).total_seconds() // (7 * 86400)) + 1)
    level = PiracyMember.LEVEL[role]
    candidates, later = [], []                          # (points, convert, crew, account); when to come back
    if role == PiracyMember.PASSER:
        window = collect_from(month, game_account, now_members, now)
        if now < window:
            later.append(window)                        # N-263: collects only in the window before its delivery
        else:
            for ga, theirs, convert in collectable(month, game_account, now_members, now, plan.get("base_cap"), mine):
                candidates.append((theirs["points"] or 0, convert, theirs["crew"], ga))
    else:
        my_passer = flow.today(month, now).get(str(game_account.pk))
        taken = _taken(month)
        for m in now_members:
            ga = m.game_account
            if m.role == PiracyMember.CHAMPION or ga.pk in taken:
                continue
            theirs = strength(ga)
            points = theirs["points"] or 0
            if m.role == PiracyMember.PASSER:
                # only its own passer of the day, at the delivery
                if str(ga.pk) != my_passer or ga.account_id == game_account.account_id or pair_release(month, game_account, ga, now):
                    continue
                when = empties_at(month, ga, now_members, now, game_account)
                if when > now:
                    later.append(when)
                    continue
                if points < month.min_points or not sure_win(mine["crew"], theirs["crew"], month.min_win_percent):
                    continue
            else:
                if points < direct_from(month, ga, now_members, now) or not champion_may_take(month, game_account, ga, now, theirs):
                    continue
                if _leave_to_other_champion(month, game_account, ga, theirs, now_members, member.group):
                    continue
            candidates.append((points, 0, theirs["crew"], ga))
    # N-255: the most points for the least conversion first (net gain)
    for points, convert, crew, ga in sorted(candidates, key=lambda c: (-(c[0] - c[1]), c[1])):
        if not _target_info(ga):
            continue
        # a conversion first takes its time: the raid waits for it
        until = now + timedelta(hours=ROUTE_HOURS * (2 if convert else 1))
        if month.turn_at and now < month.turn_at:
            until = min(until, month.turn_at)
        route = PiracyRoute.objects.create(month=month, group=member.group, week=week, stage=level, raider=game_account,
                                           target=ga, target_points=points, raider_crew=mine["crew"], target_crew=crew,
                                           due_from=now, due_until=until)
        route = PiracyRoute._base_manager.select_related("raider", "target").get(pk=route.pk)
        if role == PiracyMember.CHAMPION and str(ga.pk) in flow.still_to_pass(month, game_account, now):
            flow.replan_rest(month, now)                 # N-263: its day goes to another account
        return {"route": {**_payload(route, _target_info(ga)), "convert_points": convert}, "hold": hold, **extra}
    outside = _outside_route(month, member, game_account, mine, now, week, role)
    if outside is None and later:
        # N-263: the ship comes back for its raid at the planned time (a mission that ends after it waits)
        return {"route": None, "hold": hold, **extra, "route_at": int(min(later).timestamp())}
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


def day_agenda(month, members, now: datetime | None = None) -> list[dict]:
    """N-263: today's delivery of each champion — its passer of the day, from when the passer collects and when the
    champion's raid lands on it."""
    now = now or timezone.now()
    by_pk = {m.game_account.pk: m.game_account for m in members}
    passers = flow.today(month, now)
    out = []
    for m in members:
        if m.role != PiracyMember.CHAMPION:
            continue
        champion = m.game_account
        passer = next((ga for pk, ga in by_pk.items() if str(pk) == passers.get(str(champion.pk))), None)
        row = {"champion": champion, "passer": passer, "deliver": _hm(deliver_at(month, now), now)}
        if passer is not None:
            window = collect_from(month, passer, members, now)
            row.update(collect=_hm(window, now), collecting=now >= window, sources=collect_count(month, passer, members, now))
        out.append(row)
    return out


def matrix(month, members, rows_by_ga: dict, active: list, now: datetime | None = None) -> dict:
    """N-263: who takes whom today, cell by cell, by the same rules next_for follows — now, the pair locked until when,
    a raid on its way (landing when), at the planned time (the passer's window, the delivery), or not today and why."""
    now = now or timezone.now()
    role = {m.game_account_id: m.role for m in members}
    champions = [m.game_account for m in members if m.role == PiracyMember.CHAMPION]
    passer_of = flow.today(month, now)

    def pts(ga):
        return int((rows_by_ga.get(ga.pk) or {}).get("capture_points") or 0)

    def read(ga):
        row = rows_by_ga.get(ga.pk) or {}
        return {"crew": row.get("crew"), "points": row.get("capture_points")}

    def short(points):
        return f"<{points // 1000} mil" if points >= 1000 else f"<{points}"

    raiders = champions + sorted([m.game_account for m in members if m.role == PiracyMember.PASSER], key=lambda g: -pts(g))
    targets = sorted([m.game_account for m in members if m.role != PiracyMember.CHAMPION],
                     key=lambda g: (role[g.pk] != PiracyMember.PASSER, -pts(g)))
    on = {(r.raider_id, r.target_id): r for r in active if r.target_id}
    busy = {r.target_id: r for r in active if r.target_id}
    windows, rows = {}, []
    for raider in raiders:
        is_champion = role[raider.pk] == PiracyMember.CHAMPION
        later = flow.still_to_pass(month, raider, now) if is_champion else set()
        cells = []
        for target in targets:
            route = on.get((raider.pk, target.pk))
            release = None
            if raider.pk == target.pk or (not is_champion and role[target.pk] == PiracyMember.PASSER):
                cell = {"kind": "no", "text": "—", "title": ""}
            elif is_champion and role[target.pk] == PiracyMember.PASSER and passer_of.get(str(raider.pk)) != str(target.pk):
                cell = {"kind": "no", "text": "—", "title": "passagem da outra campeã"}
            elif raider.account_id == target.account_id:
                cell = {"kind": "no", "text": "lobby", "title": "o jogo não deixa o mesmo lobby"}
            elif route is not None:
                text = (f"chega {_hm(route.arrives_at, now)}" if route.arrives_at else "a caminho") \
                    if route.status == PiracyRoute.SENT else "sai agora"
                cell = {"kind": "go", "text": text, "title": "assalto do plano"}
            elif (release := pair_release(month, raider, target, now)) is not None:
                cell = {"kind": "lock", "text": _hm(release, now), "title": "dupla livre em"}
            elif target.pk in busy:
                cell = {"kind": "lock", "text": "ocupado", "title": f"{busy[target.pk].raider.name} já vai"}
            elif is_champion and role[target.pk] == PiracyMember.PASSER:
                when = empties_at(month, target, members, now, raider)
                if when > now:
                    cell = {"kind": "wait", "text": _hm(when, now), "title": "entrega: depois de a passagem recolher"}
                elif pts(target) < month.min_points:
                    cell = {"kind": "no", "text": short(month.min_points), "title": "abaixo do mínimo de roubo"}
                else:
                    cell = {"kind": "ok", "text": "agora", "title": "entrega"}
            elif is_champion:
                threshold = direct_from(month, target, members, now)
                if str(target.pk) in later:
                    cell = {"kind": "no", "text": "passa depois", "title": "é passagem dela mais tarde neste bloco"}
                elif pts(target) < threshold:
                    cell = {"kind": "no", "text": short(threshold), "title": "abaixo do roubo direto: espera a passagem"}
                elif not sure_win(read(raider)["crew"], read(target)["crew"], month.min_win_percent):
                    cell = {"kind": "no", "text": "não vence", "title": ""}
                else:
                    cell = {"kind": "ok", "text": "agora", "title": "roubo direto"}
            else:
                if pts(target) < month.min_points:
                    cell = {"kind": "no", "text": short(month.min_points), "title": "abaixo do mínimo de roubo"}
                elif pts(target) >= (month.direct_points or 0) and any(
                        champion_may_take(month, c, target, now, read(target)) for c in champions):
                    cell = {"kind": "no", "text": "vai direto", "title": "a campeã leva direto"}
                else:
                    convert = _convert_to_win(month, read(raider), read(target), None)
                    if raider.pk not in windows:
                        windows[raider.pk] = collect_from(month, raider, members, now)
                    if convert is None:
                        cell = {"kind": "no", "text": "não vence", "title": ""}
                    elif now < windows[raider.pk]:
                        cell = {"kind": "wait", "text": _hm(windows[raider.pk], now), "title": "recolhe a partir de"}
                    else:
                        cell = {"kind": "ok", "text": f"converte {convert:,}".replace(",", ".") if convert else "agora",
                                "title": "recolha"}
            cells.append(cell)
        rows.append({"ga": raider, "role": "campeã" if is_champion else "passagem", "cells": cells})
    return {"targets": [{"ga": t, "points": pts(t), "role": "passagem" if role[t.pk] == PiracyMember.PASSER else "origem"}
                        for t in targets], "rows": rows}


def board(month, rows_by_ga: dict, now: datetime | None = None) -> dict:
    """Everything the piracy page shows of the running plan: exposed points, the two champions side by side, the chain
    (origins -> passers -> champions) with what each account does next and when, the day's agenda, who may take whom,
    and the feed of raids."""
    now = now or timezone.now()
    flow.join_new(month)
    members = members_now(month, now)
    drop_stale(month, members)
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
    day = day_agenda(month, members, now)
    crew = crew_plan(month, now)
    deliver_to = {row["passer"].pk: row for row in day if row.get("passer") is not None}

    def read(ga):
        row = rows_by_ga.get(ga.pk) or {}
        return {"crew": row.get("crew"), "points": row.get("capture_points")}

    def lands(route) -> str:
        return f" · chega {_hm(route.arrives_at, now)}" if route.arrives_at else ""

    def node(m):
        ga, r = m.game_account, read(m.game_account)
        row = rows_by_ga.get(ga.pk) or {}
        points = int(r["points"] or 0)
        out = {"ga": ga, "role": m.role, "points": points, "crew": r["crew"], "color": color[ga.account_id],
               "lobby": ga.account.label if ga.account_id else "", "fill": min(100, round(points * 100 / scale)),
               "tone": "mut", "text": "", "read": _hm(row.get("read_at"), now),
               "stale": bool(row.get("read_at") and now - row["read_at"] > timedelta(hours=2)),
               "robbed": _hm(row.get("robbed_at"), now), "since": ""}
        if m.role != PiracyMember.CHAMPION and points:
            out["since"] = _ago(emptied.get(ga.pk) or month.starts_at, now)
        route_in, route_out = by_target.get(ga.pk), by_raider.get(ga.pk)
        if m.role == PiracyMember.CHAMPION:
            if route_out and route_out.status == PiracyRoute.SENT:
                out.update(tone="warn", text=f"assalto em {route_out.target_name} ({route_out.target_points or 0:,})".replace(",", ".") + lands(route_out))
            elif route_out:
                out.update(tone="ok", text=f"rouba {route_out.target_name} agora")
            else:
                mine = next((d for d in day if d["champion"].pk == ga.pk), None)
                if mine and mine.get("passer") is not None:
                    out.update(tone="ok", text=f"esvazia {mine['passer'].name} às {mine['deliver']}")
                else:
                    out.update(tone="ok", text="livre")
        elif m.role == PiracyMember.PASSER:
            until = _converting_until(ga)
            plan = deliver_to.get(ga.pk)
            if until > now.timestamp():
                ready = datetime.fromtimestamp(until, tz=datetime_timezone.utc)
                out.update(tone="warn", text=f"convertendo · pronta {_hm(ready, now)}")
            elif route_out:
                out.update(tone="warn" if route_out.status == PiracyRoute.SENT else "ok",
                           text=("a caminho de " if route_out.status == PiracyRoute.SENT else "rouba ") + route_out.target_name + lands(route_out))
            else:
                need = next((row for row in crew if row["day"] == 0 and row["passer"].pk == ga.pk), None)
                when = (f"recolhe às {plan['collect']} · " if plan and not plan.get("collecting") else "")
                if need is None or not need["target"]:
                    out.update(tone="mut", text=when + "sem origem do lobby da campeã hoje")
                elif need["convert"] is None:
                    out.update(tone="bad", text=when + f"não chega à força {need['target']:,}".replace(",", "."))
                elif need["convert"]:
                    out.update(tone="warn", text=when + f"converte {need['convert']:,} para força {need['target']:,}".replace(",", "."))
                else:
                    out.update(tone="ok", text=when + f"força {need['now']:,} ≥ {need['target']:,}".replace(",", "."))
        else:
            if route_in and route_in.status == PiracyRoute.SENT:
                out.update(tone="warn", text=f"{route_in.raider.name} a caminho" + lands(route_in))
            elif route_in:
                out.update(tone="ok", text=f"vai para {route_in.raider.name}")
            elif points >= month.min_points:
                out.update(tone="warn", text=row.get("why") or "aguardando")
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
            "agenda": day, "matrix": matrix(month, members, rows_by_ga, active, now), "crew_plan": crew,
            "delivered": delivered, "oldest": oldest, "deliver_at": _hm(deliver_at(month, now), now)}
