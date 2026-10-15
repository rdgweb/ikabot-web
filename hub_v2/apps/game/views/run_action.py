"""
View: RunActionView — create a job from the UI via HTMX.

Accepts POST with game_account_id + action_code (per-server jobs)
or account_id + action_code (lobby-level jobs like discover_characters).
Returns an HTMX-compatible response that triggers a toast notification.
"""

import json
from datetime import timedelta

from django.http import HttpResponse
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.views import View

from apps.accounts.models import Account, GameAccount
from apps.jobs.services.workflows import create_job_with_workflow

ACTION_NAMES = {
    100: "Check Status",
    101: "Discover Characters",
    102: "Donate",
    103: "Login Daily",
    602: "Dump Mundo",
    9002: "Revolta",
}


CHECK_STATUS = 100
CHECK_ALL_SPACING_SECONDS = 20     # one login every 20 s, so ikabotapi is never flooded


class CheckAllStatusView(LoginRequiredMixin, View):
    """N-127: "Verificar Status" for every game account on the panel (the ones the filters leave visible).

    Accounts that already have one waiting or running are skipped; the others start one after another.
    """

    def post(self, request):
        wanted = {part.strip() for part in (request.POST.get("game_account_ids") or "").split(",") if part.strip()}
        game_accounts = (GameAccount.objects.filter(active=True, account__active=True)
                         .select_related("account__node").order_by("account__label", "name"))
        if wanted:
            game_accounts = game_accounts.filter(pk__in=wanted)
        game_accounts = [ga for ga in game_accounts if ga.account.node_id and ga.account.node.active]
        if not game_accounts:
            return RunActionView._toast("Nenhuma conta com nó ativo para verificar.", "error")

        from apps.jobs.models import Job

        busy = set(Job.objects.filter(game_account__in=game_accounts, action_code=CHECK_STATUS,
                                      status__in=("queued", "scheduled", "running"))
                   .values_list("game_account_id", flat=True))
        now = timezone.now()
        started = 0
        for ga in game_accounts:
            if ga.pk in busy:
                continue
            when = now + timedelta(seconds=CHECK_ALL_SPACING_SECONDS * started) if started else None
            create_job_with_workflow(
                account=ga.account,
                game_account=ga,
                node=ga.account.node,
                action_code=CHECK_STATUS,
                inputs={},
                status="scheduled" if when else "queued",
                scheduled_for=when,
            )
            started += 1

        skipped = len(game_accounts) - started
        if not started:
            return RunActionView._toast("Todas essas contas já têm um Verificar Status na fila.", "info")
        message = f"Verificar Status pedido para {started} conta{'s' if started != 1 else ''}"
        if skipped:
            message += f" ({skipped} já estava{'m' if skipped != 1 else ''} na fila)"
        return RunActionView._toast(message + ".", "success")


class RunActionView(LoginRequiredMixin, View):
    """POST-only: create a job and return HTMX toast response."""

    def post(self, request):
        ga_id = request.POST.get("game_account_id")
        acct_id = request.POST.get("account_id")
        action_code = request.POST.get("action_code")

        if not action_code:
            return self._toast("Código de ação ausente.", "error")

        action_code = int(action_code)
        action_name = ACTION_NAMES.get(action_code, f"Action {action_code}")

        inputs = {
            k[6:]: v
            for k, v in request.POST.items()
            if k.startswith("input_")
        }

        # Per-server job (game_account_id provided)
        if ga_id:
            try:
                ga = GameAccount.objects.select_related("account__node").get(pk=ga_id)
            except GameAccount.DoesNotExist:
                return self._toast("Conta de jogo não encontrada.", "error")

            account = ga.account
            node = account.node
            if not node:
                return self._toast("Conta sem nó atribuído.", "error")

            create_job_with_workflow(
                account=account,
                game_account=ga,
                node=node,
                action_code=action_code,
                inputs=inputs,
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
                return self._toast("Conta lobby não encontrada.", "error")

            node = account.node
            if not node:
                return self._toast("Conta sem nó atribuído.", "error")

            create_job_with_workflow(
                account=account,
                node=node,
                action_code=action_code,
                inputs=inputs,
                status="queued",
            )
            return self._toast(
                f"Job '{action_name}' criado para {account.label}.",
                "success",
            )

        return self._toast("Parâmetros inválidos.", "error")

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
