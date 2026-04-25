"""
View: RunActionView — create a job from the UI via HTMX.

Accepts POST with game_account_id + action_code (per-server jobs)
or account_id + action_code (lobby-level jobs like discover_characters).
Returns an HTMX-compatible response that triggers a toast notification.
"""

import json

from django.http import HttpResponse
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views import View

from apps.accounts.models import Account, GameAccount
from apps.jobs.services.workflows import create_job_with_workflow

ACTION_NAMES = {
    100: "Check Status",
    101: "Discover Characters",
    102: "Donate",
    103: "Login Daily",
}


class RunActionView(LoginRequiredMixin, View):
    """POST-only: create a job and return HTMX toast response."""

    def post(self, request):
        ga_id = request.POST.get("game_account_id")
        acct_id = request.POST.get("account_id")
        action_code = request.POST.get("action_code")

        if not action_code:
            return self._toast("Codigo de acao ausente.", "error")

        action_code = int(action_code)
        action_name = ACTION_NAMES.get(action_code, f"Action {action_code}")

        # Per-server job (game_account_id provided)
        if ga_id:
            try:
                ga = GameAccount.objects.select_related("account__node").get(pk=ga_id)
            except GameAccount.DoesNotExist:
                return self._toast("Conta de jogo nao encontrada.", "error")

            account = ga.account
            node = account.node
            if not node:
                return self._toast("Conta sem no atribuido.", "error")

            create_job_with_workflow(
                account=account,
                game_account=ga,
                node=node,
                action_code=action_code,
                inputs={},
                status="queued",
            )
            return self._toast(
                f"Job '{action_name}' criado para {ga.name or ga.server_id}.",
                "success",
            )

        # Lobby-level job (account_id provided)
        if acct_id:
            try:
                account = Account.objects.select_related("node").get(pk=acct_id)
            except Account.DoesNotExist:
                return self._toast("Conta lobby nao encontrada.", "error")

            node = account.node
            if not node:
                return self._toast("Conta sem no atribuido.", "error")

            create_job_with_workflow(
                account=account,
                node=node,
                action_code=action_code,
                inputs={},
                status="queued",
            )
            return self._toast(
                f"Job '{action_name}' criado para {account.label}.",
                "success",
            )

        return self._toast("Parametros invalidos.", "error")

    @staticmethod
    def _toast(message: str, toast_type: str = "info") -> HttpResponse:
        """Return an empty response with HX-Trigger to show a toast."""
        import json as _json

        trigger = _json.dumps({
            "toast": {"type": toast_type, "message": message}
        })
        resp = HttpResponse(status=204)
        resp["HX-Trigger"] = trigger
        return resp
