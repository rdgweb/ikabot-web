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
PAIR_DAYS = 7                # the same raider takes from the same account once in this many days
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
    may pass points on in that group (never of the champion's lobby), so that every origin keeps someone of another
    lobby to take its points, giving the turn to who passed the least; with no such choice the passers stay."""
    from types import SimpleNamespace

    turn = rotation_index(month, now)
    stored = list(month.members.select_related("game_account__account").order_by("group", "id"))
    out = []
    for group in sorted({m.group for m in stored}):
        of = [m for m in stored if m.group == group]
        champion = next((m.game_account for m in of if m.role == PiracyMember.CHAMPION), None)
        if champion is not None:
            out.append(SimpleNamespace(game_account=champion, game_account_id=champion.pk, group=group,
                                       role=PiracyMember.CHAMPION))
        others = [m.game_account for m in of if m.role != PiracyMember.CHAMPION]
        passers = [m.game_account for m in of if m.role == PiracyMember.PASSER]
        counts = {ga.pk: (1 if ga in passers else 0) for ga in others}
        for _ in range(turn):
            passers = _next_passers(champion, others, passers, counts)
            for ga in passers:
                counts[ga.pk] += 1
        chosen = {ga.pk for ga in passers}
        for ga in others:
            out.append(SimpleNamespace(game_account=ga, game_account_id=ga.pk, group=group,
                                       role=PiracyMember.PASSER if ga.pk in chosen else PiracyMember.ORIGIN))
    return out


def _next_passers(champion, others: list, current: list, counts: dict) -> list:
    """The passers of the next turn of one group (see members_now); counts: turns each account has passed so far."""
    from itertools import combinations

    if not current:
        return []
    able = [ga for ga in others if champion is None or ga.account_id != champion.account_id]
    size = min(len(current), len(able))
    if not size:
        return current
    now_ids = {ga.pk for ga in current}

    def key(combo):
        ids = {ga.pk for ga in combo}
        links = chain(champion, list(combo), [ga for ga in others if ga.pk not in ids])
        served = sum(1 for o in others if o.pk not in ids and any(t.pk in ids for t in links["takers"].get(o.pk, [])))
        return (len(links["blocked"]), len(links["idle"]), sum(counts.get(ga.pk, 0) for ga in combo), ids == now_ids,
                -served, sorted(str(ga.pk) for ga in combo))

    options = list(combinations(sorted(able, key=lambda ga: (ga.name or "", str(ga.pk))), size))[:2000]
    best = min(options, key=key)
    if len(chain(champion, list(best), [ga for ga in others if ga not in best])["blocked"]) > \
            len(chain(champion, current, [ga for ga in others if ga not in current])["blocked"]):
        return current                                   # no choice keeps every origin reachable: the passers stay
    return list(best)


def chain(champion, passers: list, origins: list) -> dict:
    """N-215: who may take whose points in a group, by lobby alone (the game never lets two accounts of one lobby raid
    each other). takers[origin pk]: the passers of another lobby, plus the champion when of another lobby;
    sources[passer pk]: the origins of another lobby. blocked: origins nobody may take; idle: passers with no origin
    to take from; wrong: passers of the champion's lobby (the champion could never take theirs)."""
    takers = {}
    for o in origins:
        t = [p for p in passers if p.account_id != o.account_id]
        if champion is not None and champion.account_id != o.account_id:
            t.append(champion)
        takers[o.pk] = t
    sources = {p.pk: [o for o in origins if o.account_id != p.account_id] for p in passers}
    return {"takers": takers, "sources": sources,
            "blocked": [o for o in origins if not takers[o.pk]],
            "idle": [p for p in passers if not sources[p.pk]],
            "wrong": [p for p in passers if champion is not None and p.account_id == champion.account_id]}


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


def _pair_move(others, options, place, score, current):
    """The first change of two accounts at once that makes the split better: ([(ga, option), (ga, option)], score)."""
    for x, a in enumerate(others):
        for b in others[x + 1:]:
            before = (place[a.pk], place[b.pk])
            for option_a in options:
                for option_b in options:
                    if (option_a, option_b) == before or PiracyMember.PASSER not in (option_a[1], option_b[1]):
                        continue
                    place[a.pk], place[b.pk] = option_a, option_b
                    value = score(place)
                    place[a.pk], place[b.pk] = before
                    if value > current + 1e-9:
                        return [(a, option_a), (b, option_b)], value
    return None


def build_groups(accounts: list, champions: list, crews: dict | None = None) -> list[dict]:
    """Split the other accounts between the champions (N-215). The points go origin -> passer -> champion and the game
    never lets two accounts of one lobby raid each other, so: a passer is never of its champion's lobby, every origin
    has someone of another lobby to take its points (a passer, or the champion itself), every passer has an origin of
    another lobby to take from. An account of the champion's lobby may be an origin there when a passer of another
    lobby takes its points. Among the splits that respect that, the groups stay balanced and the champion beats its
    passers (by crew) with the best chance."""
    crews = crews or {}
    others = sorted([ga for ga in accounts if ga not in champions], key=lambda ga: (ga.name or "", str(ga.pk)))
    if not champions:
        return []

    def odds(a, b):
        value = win_chance(crews.get(a.pk), crews.get(b.pk))
        return value if value is not None else 0.5

    def score(place: dict) -> float:
        total, sizes = 0.0, []
        for index, champion in enumerate(champions):
            passers = [ga for ga in others if place[ga.pk] == (index, PiracyMember.PASSER)]
            origins = [ga for ga in others if place[ga.pk] == (index, PiracyMember.ORIGIN)]
            links = chain(champion, passers, origins)
            total -= 1000 * len(links["wrong"]) + 100 * len(links["blocked"]) + 20 * len(links["idle"])
            if len(passers) > PASSERS_PER_GROUP:
                total -= 50 * (len(passers) - PASSERS_PER_GROUP)
            # the plan wants passers (origins and passers take turns): each one that has origins to take from counts
            total += 15 * min(PASSERS_PER_GROUP, len(passers) - len(links["idle"]))
            # ...but not more passers than origins they serve (three passers living off one origin is no chain)
            served = sum(1 for o in origins if any(t is not champion for t in links["takers"][o.pk]))
            total -= 25 * max(0, len(passers) - served)
            # ...and the roles take turns: one account that may pass but waits its turn is worth more than one more passer
            able = sum(1 for ga in passers + origins if ga.account_id != champion.account_id)
            if passers and able > len(passers):
                total += 20
            total += 5 * sum(odds(champion, p) for p in passers)
            for o in origins:
                ways = [odds(t, o) * (1 if t is champion else odds(champion, t)) for t in links["takers"][o.pk]]
                total += 3 * max(ways, default=0)
            sizes.append(len(passers) + len(origins))
        return total - 4 * (max(sizes) - min(sizes))

    # start: each account with a champion of another lobby (fewest members first), all as origins; then improve one
    # account at a time while the split gets better
    place, filled = {}, [0] * len(champions)
    for ga in others:
        fit = [i for i, c in enumerate(champions) if c.account_id != ga.account_id] or list(range(len(champions)))
        index = min(fit, key=lambda i: (filled[i], -odds(champions[i], ga)))
        place[ga.pk] = (index, PiracyMember.ORIGIN)
        filled[index] += 1
    options = [(i, role) for i in range(len(champions)) for role in (PiracyMember.PASSER, PiracyMember.ORIGIN)]
    current = score(place)
    for _ in range(400):
        best_move, best_score = None, current
        for ga in others:
            for option in options:
                if option == place[ga.pk]:
                    continue
                before = place[ga.pk]
                place[ga.pk] = option
                value = score(place)
                place[ga.pk] = before
                if value > best_score + 1e-9:
                    best_move, best_score = (ga, option), value
        if best_move is None:
            # a passer that works often needs two accounts moved at once (the passer and an origin of another lobby)
            best_move = _pair_move(others, options, place, score, current)
            if best_move is None:
                break
            for ga, option in best_move[0]:
                place[ga.pk] = option
            current = best_move[1]
            continue
        place[best_move[0].pk] = best_move[1]
        current = best_score
    groups = []
    for index, champion in enumerate(champions):
        passers = [ga for ga in others if place[ga.pk] == (index, PiracyMember.PASSER)]
        origins = [ga for ga in others if place[ga.pk] == (index, PiracyMember.ORIGIN)]
        groups.append({"champion": champion, "members": passers + origins, "passers": passers, "origins": origins})
    return groups


@transaction.atomic
def plan_month(organization, server_id: str, *, champions: list | None = None, now: datetime | None = None,
               rng: random.Random | None = None, share_percent: int = 10, share_every_hours: int = 24,
               min_win_percent: int = 95, min_points: int = 10000):
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
                                       min_points=max(100, int(min_points or 10000)))
    crews = {ga.pk: strength(ga)["crew"] for ga in accounts}
    for number, group in enumerate(build_groups(accounts, chosen, crews), start=1):
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


# ── what a champion can gather ─────────────────────────────────────────────────────────────────────────────────────

def upcoming(month, rows_by_ga: dict) -> dict:
    """The next raids of the plan (drawn times) and the accounts over the points limit still without a raider."""
    planned = list(month.routes.filter(status__in=(PiracyRoute.PLANNED, PiracyRoute.SENT))
                   .select_related("raider", "target", "outside").order_by("due_from"))
    busy = {r.target_id for r in planned if r.target_id}
    waiting = [row for ga_id, row in rows_by_ga.items()
               if (row.get("role") in ("passagem", "origem")) and (row.get("capture_points") or 0) >= month.min_points
               and ga_id not in busy]
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
        champion = next((m.game_account for m in of if m.role == PiracyMember.CHAMPION), None)
        if champion is None:
            continue
        passers = [m.game_account for m in of if m.role == PiracyMember.PASSER]
        detail, expected, held = [], 0.0, 0
        for m in of:
            if m.role == PiracyMember.CHAMPION:
                continue
            ga = m.game_account
            held += points(ga)
            best, way = chance(champion, ga), "direto"
            if m.role == PiracyMember.ORIGIN:
                for passer in passers:
                    via = chance(passer, ga) * chance(champion, passer)
                    if via > best:
                        best, way = via, f"por {passer.name}"
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


def budgets(month, crews: dict) -> dict:
    """Per account of the month, the crew it may have (cap: its raider still wins with the month's chance) and the crew
    it needs (floor: a passer beats its origins) — what decides how much it can convert. crews: game account id -> crew.
    The champion has neither (it converts all, and gathers in its final phase)."""
    ratio = ratio_for(month.min_win_percent)
    members = members_now(month)
    out = {}
    for group in {m.group for m in members}:
        of = [m for m in members if m.group == group]
        champion = next((m.game_account for m in of if m.role == PiracyMember.CHAMPION), None)
        passers = [m.game_account for m in of if m.role == PiracyMember.PASSER]
        origins = [m.game_account for m in of if m.role == PiracyMember.ORIGIN]

        def raiders_of(ga, role):
            if role == PiracyMember.PASSER:
                return [champion] if champion and champion.account_id != ga.account_id else []
            return [p for p in passers if p.account_id != ga.account_id] + (
                [champion] if champion and champion.account_id != ga.account_id else [])

        for m in of:
            if m.role == PiracyMember.CHAMPION:
                continue
            ga = m.game_account
            raider_crews = [crews.get(r.pk) for r in raiders_of(ga, m.role) if crews.get(r.pk) is not None]
            cap = int(max(raider_crews) / ratio) if raider_crews else None
            floor = None
            if m.role == PiracyMember.PASSER:
                # the crew to beat its origins — only the ones it can beat while its champion still beats it (cap wins)
                needs = sorted(int(crews[o.pk] * ratio) + 1 for o in origins
                               if o.account_id != ga.account_id and crews.get(o.pk) is not None)
                within = [n for n in needs if cap is None or n <= cap]
                floor = within[-1] if within else None
            out[ga.pk] = {"cap": cap, "floor": floor, "crew": crews.get(ga.pk)}
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


def plan_for(game_account, now: datetime | None = None) -> dict | None:
    """The account's part in the month's plan now, read only (nothing drawn): None when it is in no running plan.
    Champions convert all; the others hold their points, converting share_percent once every share_every_hours up to
    crew_cap (in the final phase of the ranking nobody converts)."""
    now = now or timezone.now()
    member = (PiracyMember._base_manager.filter(game_account=game_account, month__status=PiracyMonth.OPEN,
                                                 month__starts_at__lte=now, month__paused=False).select_related("month").first())
    if member is None:
        return None
    month = member.month
    now_members = members_now(month, now)
    role = next((m.role for m in now_members if m.game_account_id == game_account.pk), member.role)
    hold = role != PiracyMember.CHAMPION
    out = {"member": member, "month": month, "members": now_members, "role": role, "hold": hold, "final": False,
           "convert_all": not hold, "share_percent": month.share_percent if hold else 0,
           "share_every_hours": month.share_every_hours, "crew_cap": None, "crew_floor": None}
    if hold:
        crews = {m.game_account_id: strength(m.game_account)["crew"] for m in now_members if m.group == member.group}
        mine_budget = budgets(month, crews).get(game_account.pk) or {}
        out.update(crew_cap=mine_budget.get("cap"), crew_floor=mine_budget.get("floor"))
        # in the final phase of the ranking (the points count for the position at the turn) nobody converts
        if _final_phase(game_account):
            out.update(share_percent=0, crew_floor=None, final=True)
    return out


def next_for(game_account, now: datetime | None = None) -> dict:
    """What the Pirate Fortress job of this account does for the plan now:
    {"route": {...} | None, "hold": bool, "convert_all": bool, "share_percent": int, "share_every_hours": int}.
    Champions convert all their points (convert_all) and, after gathering, raid the targets added by hand. The others
    keep their points (hold), converting share_percent of them once every share_every_hours. route: whom to raid now —
    someone earlier on the way, of another lobby, surely beaten, not raided by it in the last 7 days, with the most
    capture points."""
    now = now or timezone.now()
    expire(now)
    plan = plan_for(game_account, now)
    if plan is None:
        return {"route": None, "hold": False}
    member, month, now_members, role, hold = plan["member"], plan["month"], plan["members"], plan["role"], plan["hold"]
    extra = {k: plan[k] for k in ("convert_all", "share_percent", "share_every_hours", "crew_cap", "crew_floor")}
    open_route = (PiracyRoute._base_manager.filter(raider=game_account, month=month, status=PiracyRoute.PLANNED, due_until__gt=now)
                  .select_related("raider__account", "target__account", "outside").order_by("due_from").first())
    if open_route is not None:
        if open_route.due_from > now:                       # drawn for later: the ship must be free then
            return {"route": None, "hold": hold, **extra, "route_at": int(open_route.due_from.timestamp())}
        info = _target_info(open_route.target) if open_route.target_id else _outside_info(open_route.outside)
        if info:
            return {"route": _payload(open_route, info), "hold": hold, **extra}
    if role == PiracyMember.ORIGIN:
        return {"route": None, "hold": hold, **extra}
    level = PiracyMember.LEVEL[role]
    mine = strength(game_account)
    week = max(1, int((now - month.starts_at).total_seconds() // (7 * 86400)) + 1)
    recent = set(PiracyRoute._base_manager.filter(raider=game_account, created_at__gte=now - timedelta(days=PAIR_DAYS))
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
        if (theirs["points"] or 0) < month.min_points or not sure_win(mine["crew"], theirs["crew"], month.min_win_percent):
            continue
        candidates.append((theirs["points"], theirs["crew"], ga))
    # a random time within the next day, so the raids of the plan have no pattern — but before the ranking turns:
    # at the turn every account's capture points go back to 0, so a raid after it would take nothing
    window_end = now + timedelta(hours=DRAW_HOURS)
    if month.turn_at and now < month.turn_at:
        window_end = min(window_end, month.turn_at - TURN_MARGIN)
    if window_end <= now:
        return {"route": None, "hold": hold, **extra}
    for points, crew, ga in sorted(candidates, key=lambda c: -c[0]):
        if not _target_info(ga):
            continue
        when = now + timedelta(seconds=random.randint(0, int((window_end - now).total_seconds())))
        until = when + timedelta(hours=ROUTE_HOURS)
        if month.turn_at and now < month.turn_at:
            until = min(until, month.turn_at)
        PiracyRoute.objects.create(month=month, group=member.group, week=week, stage=level, raider=game_account,
                                   target=ga, target_points=points, raider_crew=mine["crew"], target_crew=crew,
                                   due_from=when, due_until=until)
        return {"route": None, "hold": hold, **extra, "route_at": int(when.timestamp())}
    return {"route": _outside_route(month, member, game_account, mine, now, week, role), "hold": hold, **extra}


def _outside_route(month, member, game_account, mine, now, week, role=None) -> dict | None:
    """A target added by hand, raided by a champion (the strongest crew) — not twice by the same one in 7 days."""
    if (role or member.role) != PiracyMember.CHAMPION:
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
