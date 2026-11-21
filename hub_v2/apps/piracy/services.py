"""Piracy page and the month's champions (N-210/N-211).

Read side: what the hub already receives — the ranking the Pirate Fortress job reads (snapshot.piracy_highscore), the
job's own counters (capture points, crew, missions), the pirate raids on their way (attack_alert_state) and the
piracy captchas.

Plan side: two champions a month from different lobbies (a lottery among the accounts that were not champions yet);
each group has passers (lobbies other than its champion's) and origins. Each week a passer raids an origin (stage 1)
and the champion raids the passer (stage 2). Fixed rules: the same lobby never raids itself; the same raider never
raids the same account twice in a week.
"""
from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from .models import PiracyChampion, PiracyMonth, PiracyRoute

PIRATE_ACTION = 17
CHAMPIONS = 2
PASSERS_PER_GROUP = 3
STAGE_SPLIT = 0.5          # share of each week given to stage 1 (passers take from origins) before the champion's stage
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
        rows.append({
            "ga": ga, "lobby": ga.account.label, "fortress": fortress, "room": town_room(snap.cities if snap else None),
            "place": own.get("place"), "score": own.get("score"),
            "capture_points": inputs.get("last_capture_points"), "crew": inputs.get("last_crew_points"),
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

def current_month(server_id: str):
    return PiracyMonth.objects.filter(server_id=server_id, status=PiracyMonth.OPEN).order_by("-starts_at").first()


def month_roles(month) -> dict:
    """game account id -> "campeã" | "passagem" | "origem"."""
    roles = {c.game_account_id: "campeã" for c in month.champions.all()}
    for route in month.routes.all():
        if route.stage == 2:
            roles.setdefault(route.target_id, "passagem")
        else:
            roles.setdefault(route.raider_id, "passagem")
            roles.setdefault(route.target_id, "origem")
    return roles


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


def pick_champions(accounts: list, server_id: str, rng: random.Random | None = None, room: dict | None = None) -> list:
    """Two from different lobbies, drawn among the accounts that were not champions yet (all again once everyone was)
    and that have a free town slot (a champion raids)."""
    rng = rng or random.Random()
    past = _past_champions(server_id)
    if room is not None:
        accounts = [ga for ga in accounts if (room.get(ga.pk) or 0) > 0]
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


def build_groups(accounts: list, champions: list, room: dict | None = None) -> list[dict]:
    """Split the other accounts between the champions. Who raids founds a temporary colony, so passers come only from
    accounts with a free town slot, of a lobby other than their champion's (small lobbies first, they bridge the big
    ones); the others are origins, each given to a passer of another lobby — or straight to the champion when no
    passer can take it. links: (raider, origin, stage)."""
    room = room if room is not None else {ga.pk: 1 for ga in accounts}
    can_raid = {ga.pk for ga in accounts if (room.get(ga.pk) or 0) > 0}
    others = [ga for ga in accounts if ga not in champions]
    lobby_size = defaultdict(int)
    for ga in accounts:
        lobby_size[ga.account_id] += 1
    groups = [{"champion": c, "members": [], "passers": []} for c in champions]
    # passers first: each raiding account goes to the group whose champion is of another lobby and has fewest passers
    for ga in sorted([g for g in others if g.pk in can_raid], key=lambda g: (lobby_size[g.account_id], g.name or "")):
        fit = [gr for gr in groups if gr["champion"].account_id != ga.account_id and len(gr["passers"]) < PASSERS_PER_GROUP]
        if fit:
            min(fit, key=lambda gr: len(gr["passers"]))["passers"].append(ga)
    passers_all = {p.pk for gr in groups for p in gr["passers"]}
    # origins: to the group that has someone (passer or champion) of another lobby to take them, the lightest one
    for ga in sorted([g for g in others if g.pk not in passers_all], key=lambda g: (-lobby_size[g.account_id], g.name or "")):
        fit = [gr for gr in groups if any(p.account_id != ga.account_id for p in gr["passers"])
               or gr["champion"].account_id != ga.account_id]
        (min(fit, key=lambda gr: len(gr["members"])) if fit else groups[0])["members"].append(ga)
    for group in groups:
        champion = group["champion"]
        links, load = [], defaultdict(int)
        for origin in group["members"]:
            able = [p for p in group["passers"] if p.account_id != origin.account_id]
            if able:
                passer = min(able, key=lambda p: (load[p.pk], p.name or ""))
                load[passer.pk] += 1
                links.append((passer, origin, 1))
            elif champion.account_id != origin.account_id:
                links.append((champion, origin, 2))                  # no passer can take it: straight to the champion
            else:
                links.append((None, origin, 0))
        group["links"] = links
        group["members"] = group["members"] + group["passers"]
    return groups


@transaction.atomic
def plan_month(organization, server_id: str, *, champions: list | None = None, now: datetime | None = None,
               rng: random.Random | None = None):
    """Close the open month of that server (its planned routes are skipped) and plan a new one."""
    now = now or timezone.now()
    old = current_month(server_id)
    if old is not None:
        close_month(old, "substituído por um novo plano")
    accounts = eligible(server_id)
    if len({ga.account_id for ga in accounts}) < 2 or len(accounts) < 4:
        raise ValueError("Faltam contas com fortaleza pirata e Pirata Automático rodando (pelo menos 4, de 2 lobbys).")
    room = rooms(accounts)
    chosen = list(champions) if champions else pick_champions(accounts, server_id, rng, room)
    if len(chosen) != CHAMPIONS or chosen[0].account_id == chosen[1].account_id:
        raise ValueError("São 2 campeãs de lobbys diferentes (com vaga de cidade no palácio).")
    if any(ga not in accounts for ga in chosen):
        raise ValueError("A campeã precisa ter fortaleza pirata e o Pirata Automático rodando.")
    if any((room.get(ga.pk) or 0) <= 0 for ga in chosen):
        raise ValueError("A campeã precisa de uma vaga de cidade no palácio: o assalto sai de uma colônia temporária.")
    starts, ends = month_window(server_id, now)
    month = PiracyMonth.objects.create(organization=organization, server_id=server_id, starts_at=starts, ends_at=ends,
                                       chosen_by="escolha" if champions else "sorteio")
    groups = build_groups(accounts, chosen, room)
    weeks = max(1, math.ceil((ends - starts).total_seconds() / (7 * 86400)))
    span = (ends - starts) / weeks
    left_out = []
    for number, group in enumerate(groups, start=1):
        PiracyChampion.objects.create(month=month, game_account=group["champion"], group=number)
        for week in range(1, weeks + 1):
            week_start = starts + span * (week - 1)
            middle = week_start + span * STAGE_SPLIT
            week_end = week_start + span
            for raider, origin, stage in group["links"]:
                if raider is None:
                    continue
                PiracyRoute.objects.create(month=month, group=number, week=week, stage=stage, raider=raider, target=origin,
                                           due_from=week_start if stage == 1 else middle,
                                           due_until=middle if stage == 1 else week_end)
            for passer in group["passers"]:
                PiracyRoute.objects.create(month=month, group=number, week=week, stage=2, raider=group["champion"],
                                           target=passer, due_from=middle, due_until=week_end)
        left_out += [origin.name for raider, origin, _stage in group["links"] if raider is None]
    no_room = [ga.name for ga in accounts if (room.get(ga.pk) or 0) <= 0]
    notes = []
    if no_room:
        notes.append("Sem vaga de cidade no palácio (só são roubadas, não roubam): " + ", ".join(no_room))
    if left_out:
        notes.append("Sem quem pegue de outro lobby: " + ", ".join(left_out))
    if notes:
        month.notes = ". ".join(notes)
        month.save(update_fields=["notes", "updated_at"])
    return month


def close_month(month, reason: str = "encerrado") -> None:
    month.routes.filter(status=PiracyRoute.PLANNED).update(status=PiracyRoute.SKIPPED, message=reason[:255])
    month.status = PiracyMonth.CLOSED
    month.save(update_fields=["status", "updated_at"])


def expire(now: datetime | None = None) -> int:
    """Routes whose window passed without a raid are skipped; months past their end close."""
    now = now or timezone.now()
    skipped = PiracyRoute._base_manager.filter(status=PiracyRoute.PLANNED, due_until__lt=now).update(
        status=PiracyRoute.SKIPPED, message="o prazo da etapa passou")
    for month in PiracyMonth._base_manager.filter(status=PiracyMonth.OPEN, ends_at__lt=now):
        close_month(month, "o ranking virou")
    return skipped


# ── the agent's side ───────────────────────────────────────────────────────────────────────────────────────────────

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


def next_for(game_account, now: datetime | None = None) -> dict:
    """What the Pirate Fortress job of this account does for the plan now: {"route": {...} | None, "hold": bool}.
    hold: this account will be raided by the plan this week — keep the capture points instead of converting them."""
    now = now or timezone.now()
    expire(now)
    route = (PiracyRoute._base_manager.filter(raider=game_account, status=PiracyRoute.PLANNED, month__status=PiracyMonth.OPEN,
                                              due_from__lte=now, due_until__gt=now)
             .select_related("target__account", "raider__account").order_by("due_until").first())
    hold = PiracyRoute._base_manager.filter(target=game_account, status__in=(PiracyRoute.PLANNED, PiracyRoute.SENT),
                                            month__status=PiracyMonth.OPEN, due_until__gt=now,
                                            due_from__lte=now + timedelta(days=4)).exists()
    payload = None
    while route is not None:
        if route.raider.account_id == route.target.account_id:          # never inside a lobby, whatever the plan says
            route.status, route.message = PiracyRoute.SKIPPED, "mesmo lobby"
            route.save(update_fields=["status", "message", "updated_at"])
        else:
            info = _target_info(route.target)
            if info:
                payload = {"route_id": str(route.pk), "target_game_account_id": str(route.target_id),
                           "raider_lobby": str(route.raider.account_id), "target_lobby": str(route.target.account_id),
                           "stage": route.stage, "week": route.week, **info}
                break
            route.status, route.message = PiracyRoute.SKIPPED, "a conta alvo não tem fortaleza pirata conhecida"
            route.save(update_fields=["status", "message", "updated_at"])
        route = (PiracyRoute._base_manager.filter(raider=game_account, status=PiracyRoute.PLANNED, month__status=PiracyMonth.OPEN,
                                                  due_from__lte=now, due_until__gt=now)
                 .select_related("target__account", "raider__account").order_by("due_until").first())
    return {"route": payload, "hold": hold}


def report(route, status: str, message: str = "") -> None:
    """The agent says how the raid went: sent (on its way), done (raided, colony left), failed."""
    now = timezone.now()
    if status == PiracyRoute.SENT:
        route.status, route.sent_at = status, now
    elif status in (PiracyRoute.DONE, PiracyRoute.FAILED):
        route.status, route.done_at = status, now
    else:
        return
    route.message = (message or "")[:255]
    route.save(update_fields=["status", "sent_at", "done_at", "message", "updated_at"])
