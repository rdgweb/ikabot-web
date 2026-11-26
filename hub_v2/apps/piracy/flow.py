"""The plan's day (N-263): how the capture points reach the champions every day.

1. The day turns at month.turn_hour on the game's clock (GAME_TZ); the champions empty their passers DELIVER_MARGIN before it.
2. A 3-day agenda (the pair rule's length) is drawn at the start of each block: every day each champion has its passer
   of the day — another lobby, the pair free on the day it delivers, each passer once per block for the same champion.
3. A champion raids an origin of another lobby directly only from month.direct_points, and never an account that is
   still to be its passer in the block (the pair would be locked on that day). Below it the origin waits for a passer;
   an origin no passer of the day can take goes to the champion from month.min_points.
4. A passer collects only in the window before its delivery: deliver_at - (raids it makes x the time each raid takes).
5. An account that gets a Pirate Fortress and its job joins the plan as an origin and the next agenda.

Fixed by the game: the same lobby never raids itself; the same raider takes from the same account once in pair_days;
a conversion only protects the points when it is over.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import PiracyMember, PiracyMonth, PiracyRoute

GAME_TZ = ZoneInfo("Europe/Berlin")   # the game's clock (GMT+1 in the winter, as the game shows); the page shows the hub's
DELIVER_MARGIN = timedelta(hours=1)   # the champions' raids on their passers land this long before the day turns
RAID_DEFAULT = 3600                   # seconds a raid takes, out and back, when the account has none measured yet


# ── the day and the block ─────────────────────────────────────────────────────────────────────────────────────────

def day_start(month, now: datetime | None = None) -> datetime:
    """When the day running at `now` began: month.turn_hour on the GAME's clock (the page shows it in the hub's)."""
    local = (now or timezone.now()).astimezone(GAME_TZ)
    start = local.replace(hour=int(month.turn_hour or 0) % 24, minute=0, second=0, microsecond=0)
    return start if start <= local else start - timedelta(days=1)


def turn_end(month, now: datetime | None = None) -> datetime:
    """When the day running at `now` ends — or the plan (the ranking turns), if sooner."""
    end = day_start(month, now) + timedelta(days=1)
    return min(end, month.ends_at) if month.ends_at else end


def deliver_at(month, now: datetime | None = None) -> datetime:
    return turn_end(month, now) - DELIVER_MARGIN


def day_number(month, now: datetime | None = None) -> int:
    """0 for the day the plan started in, 1 for the next one..."""
    return max(0, (day_start(month, now) - day_start(month, month.starts_at)).days)


def _block(month, now=None) -> tuple[int, int]:
    """(block number, day inside it)."""
    length = max(1, month.pair_days or 3)
    return divmod(day_number(month, now), length)


# ── the members ───────────────────────────────────────────────────────────────────────────────────────────────────

def join_new(month) -> int:
    """The plan follows the accounts whose Pirate Fortress job is active: one that got its fortress and job enters as
    an origin; an origin or passer whose job is no longer active leaves (it comes back when the job runs again).
    The champions stay — a champion without its job shows on the page instead. Returns how many changed."""
    from . import services

    able = {ga.pk: ga for ga in services.eligible(month.server_id) if ga.account.organization_id == month.organization_id}
    stored = list(month.members.all())
    have = {m.game_account_id for m in stored}
    changed = 0
    for pk, ga in able.items():
        if pk not in have:
            PiracyMember._base_manager.create(month=month, game_account=ga, role=PiracyMember.ORIGIN, group=1)
            changed += 1
    gone = [m.pk for m in stored if m.role != PiracyMember.CHAMPION and m.game_account_id not in able]
    if gone:
        PiracyMember._base_manager.filter(pk__in=gone).delete()
        changed += len(gone)
    return changed


def _stored(month) -> list:
    return list(month.members.select_related("game_account__account").order_by("id"))


def champions_of(month) -> list:
    return [m.game_account for m in _stored(month) if m.role == PiracyMember.CHAMPION]


def others_of(month) -> list:
    return [m.game_account for m in _stored(month) if m.role != PiracyMember.CHAMPION]


# ── the agenda ────────────────────────────────────────────────────────────────────────────────────────────────────

def last_raid(month, raider, target):
    """When this raider last took (or tried) from this account in any plan (the pair rule follows the game)."""
    route = (PiracyRoute._base_manager.filter(raider=raider, target=target).exclude(status=PiracyRoute.SKIPPED)
             .order_by("-created_at").first())
    return route.created_at if route else None


def _free_on(month, raider, target, when) -> bool:
    last = last_raid(month, raider, target)
    return last is None or last + timedelta(days=month.pair_days or 3) <= when


def _able(month, passer, others) -> int:
    """How many accounts of another lobby the passer can beat, converting its own points if needed."""
    from . import services

    mine = services.strength(passer)
    count = 0
    for ga in others:
        if ga.pk == passer.pk or ga.account_id == passer.account_id:
            continue
        if services._convert_to_win(month, mine, services.strength(ga), None) is not None:
            count += 1
    return count


def build_block(month, number: int) -> list[dict]:
    """The agenda of one block: for each of its days, {champion pk: passer pk} (a champion may have none)."""
    champions, others = champions_of(month), others_of(month)
    length = max(1, month.pair_days or 3)
    first = day_start(month, month.starts_at) + timedelta(days=number * length)
    served = {}
    for key, days in (month.turns or {}).items():
        if key.startswith("block:"):
            for day in days:
                for passer in day.values():
                    served[passer] = served.get(passer, 0) + 1
    able = {ga.pk: _able(month, ga, others) for ga in others}
    slots = [(d, c) for d in range(length) for c in champions]
    best = {"filled": -1, "plan": {}}
    plan = {}

    def options(d, champion):
        deliver = first + timedelta(days=d + 1) - DELIVER_MARGIN
        taken_today = {plan.get((d, c.pk)) for c in champions}
        mine = {plan.get((x, champion.pk)) for x in range(length)}
        # the ability to beat the others only orders them: the points it converts with come in during the day
        out = [p for p in others if p.account_id != champion.account_id and p.pk not in taken_today
               and p.pk not in mine and _free_on(month, champion, p, deliver)]
        return sorted(out, key=lambda p: (served.get(str(p.pk), 0), -able[p.pk], p.name or "", str(p.pk)))

    def walk(i, filled):
        if filled + (len(slots) - i) <= best["filled"]:
            return
        if i == len(slots):
            best.update(filled=filled, plan=dict(plan))
            return
        d, champion = slots[i]
        for p in options(d, champion)[:4]:
            plan[(d, champion.pk)] = p.pk
            walk(i + 1, filled + 1)
            del plan[(d, champion.pk)]
        walk(i + 1, filled)

    walk(0, 0)
    return [{str(c.pk): str(best["plan"][(d, c.pk)]) for c in champions if (d, c.pk) in best["plan"]}
            for d in range(length)]


def agenda(month, now: datetime | None = None) -> list[dict]:
    """The running block's agenda, drawn once (kept in month.turns)."""
    number, _day = _block(month, now)
    key = f"block:{number}"
    turns = dict(month.turns or {})
    if key not in turns:
        turns[key] = build_block(month, number)
        month.turns = turns
        if month.pk:
            PiracyMonth._base_manager.filter(pk=month.pk).update(turns=turns)
    return turns[key]


def today(month, now: datetime | None = None) -> dict:
    """{champion pk: passer pk} of the day (strings)."""
    _number, day = _block(month, now)
    days = agenda(month, now)
    return days[day] if day < len(days) else {}


def still_to_pass(month, champion, now: datetime | None = None) -> set:
    """Accounts still to be this champion's passer later in the block — it never raids them directly."""
    _number, day = _block(month, now)
    return {row[str(champion.pk)] for row in agenda(month, now)[day + 1:] if str(champion.pk) in row}


def members_today(month, now: datetime | None = None) -> list:
    """The month's accounts with their role today: champions, the passers of the day, and the origins."""
    stored = _stored(month)
    passers = set(today(month, now).values())
    out = []
    for m in stored:
        role = m.role if m.role == PiracyMember.CHAMPION else (
            PiracyMember.PASSER if str(m.game_account_id) in passers else PiracyMember.ORIGIN)
        out.append(SimpleNamespace(game_account=m.game_account, game_account_id=m.game_account_id, group=m.group or 1,
                                   role=role))
    return out


def champion_of(month, passer, now: datetime | None = None):
    """The champion this passer delivers to today (its pk as a string), or None."""
    for champion, p in today(month, now).items():
        if p == str(passer.pk):
            return champion
    return None


# ── the times ─────────────────────────────────────────────────────────────────────────────────────────────────────

def raid_seconds(raider) -> int:
    """How long a raid of this account takes, out and back: the median of its last raids (sent -> back)."""
    if raider is None:
        return RAID_DEFAULT
    took = []
    for sent, done in (PiracyRoute._base_manager.filter(raider=raider, sent_at__isnull=False, done_at__isnull=False)
                       .order_by("-sent_at").values_list("sent_at", "done_at")[:9]):
        seconds = (done - sent).total_seconds()
        if 300 <= seconds <= 6 * 3600:
            took.append(seconds)
    took.sort()
    return int(took[len(took) // 2]) if took else RAID_DEFAULT
