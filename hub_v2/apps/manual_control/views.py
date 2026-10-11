"""Hub pages of the manual control (N-72): the button, the preparation page, the end."""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.accounts.models import GameAccount

from . import services
from .models import ManualControlSession

END_REASONS = {
    "closed": "Controle manual encerrado. A automação da conta voltou.",
    "expired": "A aba do jogo deixou de responder e o controle manual foi encerrado. A automação da conta voltou.",
    "idle": "O controle manual ficou sem uso e foi encerrado. A automação da conta voltou.",
    "cancelled": "Pedido de controle manual cancelado. A automação da conta continua.",
    "error": "Não foi possível abrir o controle manual. A automação da conta continua.",
}


def _owns(session: ManualControlSession, user) -> bool:
    return bool(user.is_superuser or session.user_id is None or session.user_id == user.pk)


def _account_label(session: ManualControlSession) -> str:
    game_account = session.game_account
    return f"{game_account.name or game_account.server_id} ({game_account.server_id})"


class StartControlView(LoginRequiredMixin, View):
    """POST: take the account over. Opened in a new tab, which then becomes the game."""

    def post(self, request, game_account_id):
        game_account = get_object_or_404(GameAccount.objects.select_related("account__node"), pk=game_account_id)
        try:
            session = services.request_control(game_account, request.user)
        except services.ControlRefused as exc:
            return render(request, "manual_control/session.html", {
                "refused": str(exc),
                "account_label": f"{game_account.name or game_account.server_id} ({game_account.server_id})",
            }, status=409)
        return redirect("manual_control:session", session_id=session.pk)


class SessionPageView(LoginRequiredMixin, View):
    def get(self, request, session_id):
        session = get_object_or_404(ManualControlSession.objects.select_related("game_account"), pk=session_id)
        return render(request, "manual_control/session.html", {
            "session": session,
            "account_label": _account_label(session),
        })


class SessionStatusView(LoginRequiredMixin, View):
    """GET: one step of the preparation and what to show; the way in once it is ready."""

    def get(self, request, session_id):
        session = get_object_or_404(ManualControlSession, pk=session_id)
        if not _owns(session, request.user):
            return JsonResponse({"state": "ended", "message": "Esta sessão pertence a outro usuário."}, status=403)
        state = services.advance(session.pk)
        if state["state"] == "active":
            session.refresh_from_db()
            state["url"] = services.new_start_url(session)
        elif state["state"] == "ended":
            state["message"] = END_REASONS.get(state.get("reason") or "", END_REASONS["closed"])
        return JsonResponse(state)


class EndControlView(LoginRequiredMixin, View):
    def post(self, request, session_id):
        session = get_object_or_404(ManualControlSession, pk=session_id)
        if not _owns(session, request.user):
            return JsonResponse({"ok": False}, status=403)
        reason = "cancelled" if session.status == ManualControlSession.PAUSING else "closed"
        services.end(session, reason)
        if request.headers.get("HX-Request"):
            return _chip(request, session.game_account_id)
        return JsonResponse({"ok": True})


class ControlChipView(LoginRequiredMixin, View):
    """GET: the control button/state of one account, for the account header."""

    def get(self, request, game_account_id):
        return _chip(request, game_account_id)


def _chip(request, game_account_id):
    services.sweep_expired(game_account_id=game_account_id)
    session = services.open_session_for(game_account_id)
    return render(request, "manual_control/chip.html", {
        "game_account_id": str(game_account_id),
        "control_enabled": services.gateway_enabled(),
        "control_session": session,
        "control_state": services.control_state(game_account_id) if session else "automation",
    })
