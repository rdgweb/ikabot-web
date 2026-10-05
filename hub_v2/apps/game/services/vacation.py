"""Estado de ferias da conta de jogo (N-68).

O jogo bloqueia o login de uma conta em modo ferias; o agent avisa o hub
quando ve esse bloqueio e o primeiro login bem-sucedido encerra o estado.
Fica em AccountSnapshot.base_snapshot["vacation_state"], como o
attack_alert_state do marcador de ataque:

    {"active": True,  "since": iso, "checked_at": iso, "source": "login_blocked"}
    {"active": False, "since": iso, "ended_at": iso}
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.game.models import AccountSnapshot
from apps.game.services.dashboard_cache import bump_dashboard_cache_version

# While the account stays on vacation every blocked login reports again;
# refreshing checked_at more often than this is noise.
RECHECK_WRITE_INTERVAL = timedelta(minutes=10)

# The game refuses logins for this long after vacation starts; after it, the
# first login (any action) ends the vacation.
MANDATORY_PERIOD = timedelta(hours=48)


def read_vacation_state(base_snapshot) -> dict:
    """{"active", "since", "checked_at", "mandatory_until", "mandatory_over"} for display."""
    raw = (base_snapshot or {}).get("vacation_state") if isinstance(base_snapshot, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    since = parse_datetime(str(raw.get("since") or "")) if raw.get("since") else None
    mandatory_until = since + MANDATORY_PERIOD if since else None
    return {
        "active": bool(raw.get("active")),
        "since": since,
        "checked_at": parse_datetime(str(raw.get("checked_at") or "")) if raw.get("checked_at") else None,
        # latest possible end: "since" is when the hub first saw the vacation
        "mandatory_until": mandatory_until,
        "mandatory_over": bool(mandatory_until and timezone.now() >= mandatory_until),
    }


def set_vacation_state(game_account, active: bool, *, source: str = "") -> bool:
    """Record that the account is (or is no longer) on vacation. True if it changed."""
    now = timezone.now()
    with transaction.atomic():
        snapshot = AccountSnapshot.objects.select_for_update().filter(game_account=game_account).first()
        if snapshot is None:
            return False
        base = dict(snapshot.base_snapshot or {})
        current = base.get("vacation_state") if isinstance(base.get("vacation_state"), dict) else {}
        was_active = bool(current.get("active"))

        if active and was_active:
            checked = parse_datetime(str(current.get("checked_at") or "")) if current.get("checked_at") else None
            if checked and now - checked < RECHECK_WRITE_INTERVAL:
                return False
            base["vacation_state"] = {**current, "checked_at": now.isoformat()}
            snapshot.base_snapshot = base
            snapshot.save(update_fields=["base_snapshot"])
            return False
        if not active and not was_active:
            return False

        if active:
            base["vacation_state"] = {
                "active": True,
                "since": now.isoformat(),
                "checked_at": now.isoformat(),
                "source": str(source or "login_blocked")[:40],
            }
        else:
            base["vacation_state"] = {
                "active": False,
                "since": current.get("since") or "",
                "ended_at": now.isoformat(),
            }
        snapshot.base_snapshot = base
        snapshot.save(update_fields=["base_snapshot"])
    bump_dashboard_cache_version()
    return True
