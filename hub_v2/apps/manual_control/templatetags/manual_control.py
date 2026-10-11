from django import template

from apps.manual_control import services
from apps.manual_control.models import ManualControlSession

register = template.Library()


@register.inclusion_tag("manual_control/chip.html", takes_context=True)
def control_chip(context, game_account_id):
    """The control button/state of one account. One query per page, however many accounts it shows."""
    request = context.get("request")
    sessions = getattr(request, "_manual_control_open", None)
    if sessions is None:
        sessions = {
            str(session.game_account_id): session
            for session in ManualControlSession.objects.filter(status__in=ManualControlSession.OPEN_STATUSES)
        }
        if request is not None:
            request._manual_control_open = sessions
    session = sessions.get(str(game_account_id))
    state = {"pausing": "pausing", "active": "manual", "resuming": "resuming"}.get(session.status) if session else "automation"
    return {
        "game_account_id": str(game_account_id),
        "control_enabled": services.gateway_enabled(),
        "control_session": session,
        "control_state": state or "automation",
        "csrf_token": context.get("csrf_token", ""),
    }
