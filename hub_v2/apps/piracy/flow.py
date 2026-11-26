"""The plan's day (N-263): when it turns (month.turn_hour on the game's clock, GAME_TZ), when the champions are
delivered (DELIVER_MARGIN before it), who is in the plan (the accounts whose Pirate Fortress job is active) and how
long a raid takes. Who takes whom each day is the day's tree (tree.py).

Fixed by the game: the same lobby never raids itself; the same raider takes from the same account once in pair_days;
a conversion only protects the points when it is over.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import PiracyMember, PiracyRoute

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
