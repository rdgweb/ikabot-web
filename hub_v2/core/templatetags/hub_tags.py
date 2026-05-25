"""
Custom template tags and filters for the hub.
"""

from django import template
from django.utils import timezone

register = template.Library()

RESOURCE_NAMES = {
    0: "wood",
    1: "wine",
    2: "marble",
    3: "crystal",
    4: "sulfur",
}

TRADEGOOD_ICONS = {
    0: "game/resources/icon_wood.png",
    1: "game/resources/icon_wine.png",
    2: "game/resources/icon_marble.png",
    3: "game/resources/icon_glass.png",
    4: "game/resources/icon_sulfur.png",
}

STATUS_CLASSES = {
    # Job statuses
    "queued": "badge-info",
    "running": "badge-warning",
    "scheduled": "bg-purple-100 text-purple-800",
    "finished": "badge-success",
    "error": "badge-danger",
    "cancelled": "badge-secondary",
    # Workflow statuses
    "draft": "badge-secondary",
    "active": "badge-info",
    "paused": "badge-secondary",
    "waiting": "badge-warning",
    "problem": "badge-danger",
}


@register.filter
def resource_name(idx):
    """Convert resource index to name: {{ 0|resource_name }} → 'wood'"""
    return RESOURCE_NAMES.get(idx, f"resource_{idx}")


@register.filter
def resource_icon(idx):
    """Return path to resource icon: {{ 0|resource_icon }}"""
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        return ""
    return TRADEGOOD_ICONS.get(idx, "")


@register.filter
def tradegood_icon(tradegood_id):
    """Return static path for tradegood icon: {{ 3|tradegood_icon }} → 'game/resources/icon_glass.png'"""
    try:
        tid = int(tradegood_id)
    except (TypeError, ValueError):
        return ""
    return TRADEGOOD_ICONS.get(tid, "")


_EXIT_CODE_LABELS = {
    0: ("success", "Concluído com sucesso"),
    1: ("error", "Erro de execução"),
    97: ("info", "Cancelado — executado imediatamente"),
    98: ("info", "Cancelado — job órfão recuperado pelo hub"),
    99: ("info", "Cancelado — duplicata removida"),
}


@register.filter
def exit_code_label(code):
    """Return (badge_type, human_label) for a job exit code."""
    if code is None:
        return None
    try:
        code = int(code)
    except (TypeError, ValueError):
        return None
    return _EXIT_CODE_LABELS.get(code)


@register.filter
def status_badge_class(status):
    """Return Tailwind classes for a status badge."""
    return STATUS_CLASSES.get(status, "bg-gray-100 text-gray-800")


@register.filter
def localtime_format(dt, fmt="%d/%m/%Y %H:%M"):
    """Format a datetime to local timezone."""
    if dt is None:
        return ""
    local_dt = timezone.localtime(dt)
    return local_dt.strftime(fmt)


@register.filter
def building_name(building_id):
    """Return localized building name: {{ 'townHall'|building_name }} → 'Prefeitura'"""
    from core.contracts import get_building_info
    return get_building_info(building_id)["name"]


@register.filter
def building_icon(building_id):
    """Return static path for building icon, or None: {{ 'academy'|building_icon }}"""
    from core.contracts import get_building_info
    info = get_building_info(building_id)
    icon = info.get("icon")
    return f"game/buildings/{icon}" if icon else None


@register.filter
def building_bi_icon(building_id):
    """Return Bootstrap Icon class for buildings without images: {{ 'shipyard'|building_bi_icon }}"""
    from core.contracts import get_building_info
    info = get_building_info(building_id)
    return info.get("bi") or "bi-building"


@register.filter
def unit_name(unit_id):
    """Return localized unit name: {{ 'Hoplite'|unit_name }} → 'Hoplita'"""
    from core.contracts import get_unit_info
    return get_unit_info(unit_id)["name"]


@register.filter
def unit_icon(unit_id):
    """Return static path for a unit icon with fallback."""
    from core.contracts import get_unit_info
    return get_unit_info(unit_id).get("icon")


@register.filter
def action_name(action_code):
    """Return localized action name: {{ 100|action_name }} → 'Verificar Status'"""
    from core.contracts import ACTION_CATALOG
    try:
        code = int(action_code)
    except (TypeError, ValueError):
        return str(action_code)
    info = ACTION_CATALOG.get(code)
    return info["name"] if info else f"Acao #{code}"


@register.filter
def duration_human(seconds):
    """Convert seconds to human-readable duration."""
    if seconds is None:
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours < 24:
        return f"{hours}h {minutes}m"
    days = hours // 24
    remaining_hours = hours % 24
    return f"{days}d {remaining_hours}h"


@register.filter
def resource_projection(resource_list, key):
    """Return a resource projection dict by key from city.resource_projections."""
    if not isinstance(resource_list, list):
        return None
    for item in resource_list:
        if isinstance(item, dict) and item.get("key") == key:
            return item
    return None


@register.filter
def get_item(mapping, key):
    """Return mapping[key] safely inside templates."""
    if isinstance(mapping, dict):
        return mapping.get(key)
    return None


@register.filter
def hours_display(hours):
    """Convert decimal hours to human-readable: 148.5 → '6d 4h 30min'."""
    if hours is None:
        return ""
    try:
        hours = float(hours)
    except (TypeError, ValueError):
        return ""
    if hours < 0:
        return ""

    total_minutes = int(hours * 60)
    if total_minutes == 0:
        return "0min"

    years, total_minutes = divmod(total_minutes, 525600)  # 365 * 24 * 60
    months, total_minutes = divmod(total_minutes, 43200)  # 30 * 24 * 60
    days, total_minutes = divmod(total_minutes, 1440)     # 24 * 60
    hrs, mins = divmod(total_minutes, 60)

    parts = []
    if years:
        parts.append(f"{years}a")
    if months:
        parts.append(f"{months}M")
    if days:
        parts.append(f"{days}d")
    if hrs:
        parts.append(f"{hrs}h")
    if mins and not years and not months:
        parts.append(f"{mins}min")
    return " ".join(parts[:2]) if parts else "0min"


@register.filter
def fill_color(pct):
    """Return Tailwind color class based on warehouse fill percentage."""
    try:
        pct = int(pct)
    except (TypeError, ValueError):
        return "text-ink"
    if pct >= 95:
        return "text-red-600 font-bold"
    if pct >= 80:
        return "text-orange-500 font-semibold"
    if pct >= 60:
        return "text-amber-600"
    return "text-ink"


@register.filter
def from_timestamp(ts):
    """Convert unix timestamp to aware datetime (local tz)."""
    if not ts:
        return None
    import datetime as _dt
    try:
        naive = _dt.datetime.utcfromtimestamp(int(ts))
        return timezone.make_aware(naive, timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


@register.filter
def timestamp_since(ts):
    """Return 'X tempo atrás' string for a unix timestamp."""
    if not ts:
        return ""
    import datetime as _dt
    try:
        naive = _dt.datetime.utcfromtimestamp(int(ts))
        dt = timezone.make_aware(naive, timezone.utc)
        now = timezone.now()
        delta = now - dt
        secs = int(delta.total_seconds())
        if secs < 60:
            return f"{secs}s atrás"
        if secs < 3600:
            return f"{secs // 60}min atrás"
        if secs < 86400:
            h = secs // 3600
            m = (secs % 3600) // 60
            return f"{h}h {m}min atrás"
        d = secs // 86400
        return f"{d}d atrás"
    except (TypeError, ValueError, OSError):
        return ""


@register.filter
def fill_bar_color(pct):
    """Return Tailwind bg class for a progress bar based on fill %."""
    try:
        pct = int(pct)
    except (TypeError, ValueError):
        return "bg-blue-400"
    if pct >= 95:
        return "bg-red-500"
    if pct >= 80:
        return "bg-orange-400"
    if pct >= 60:
        return "bg-amber-400"
    return "bg-blue-400"
