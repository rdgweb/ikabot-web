"""N-184: unit names and status chips for the Generals Bank pages (the hub CSS is precompiled: no badge-* classes)."""
from django import template
from django.utils.html import format_html

register = template.Library()

TONES = {
    "good": ("#2f6b2f", "rgba(95,140,78,.14)"),
    "bad": ("#9a3b2e", "rgba(176,74,58,.14)"),
    "warn": ("#8a5a12", "rgba(201,146,46,.16)"),
    "sea": ("#2d5f86", "rgba(79,134,185,.14)"),
    "muted": ("#6b6357", "rgba(122,114,100,.14)"),
}
STATUS_TONE = {
    "completed": "good", "sold": "good",
    "failed": "bad",
    "cancelled": "muted",
    "sleeping": "warn", "bank_buying": "warn", "training": "warn", "transporting": "warn",
    "listed": "sea", "consolidating": "sea", "listing": "sea", "bank_listing": "sea", "buyers_buying": "sea",
}


@register.filter
def unit_label(unit_id):
    from apps.generals_bank.services import unit_name

    try:
        return unit_name(int(unit_id))
    except (TypeError, ValueError):
        return str(unit_id)


@register.simple_tag
def status_chip(status, label=""):
    color, background = TONES[STATUS_TONE.get(str(status or ""), "muted")]
    return format_html(
        '<span style="display:inline-flex;align-items:center;padding:.1rem .55rem;border-radius:999px;font-size:.7rem;'
        'font-weight:700;color:{};background:{};white-space:nowrap;">{}</span>', color, background, label or status)
