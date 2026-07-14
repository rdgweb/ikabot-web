"""View: RenameCityView — renomeia uma cidade via job (N-49).

POST game_account_id + city_id + name -> cria job ac=33 e atualiza o nome
no snapshot de forma otimista para o painel refletir na hora.
"""

import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.views import View

from apps.accounts.models import GameAccount
from apps.game.models import AccountSnapshot
from apps.jobs.services.workflows import create_job_with_workflow


class RenameCityView(LoginRequiredMixin, View):
    def post(self, request):
        ga_id = request.POST.get("game_account_id")
        city_id = str(request.POST.get("city_id") or "").strip()
        name = str(request.POST.get("name") or "").strip()[:15]

        if not ga_id or not city_id or not name:
            return self._toast("Dados incompletos para renomear.", "error")

        try:
            ga = GameAccount.objects.select_related("account__node").get(pk=ga_id)
        except GameAccount.DoesNotExist:
            return self._toast("Conta nao encontrada.", "error")
        if not ga.account.node:
            return self._toast("Conta sem no atribuido.", "error")

        create_job_with_workflow(
            account=ga.account,
            game_account=ga,
            node=ga.account.node,
            action_code=33,
            inputs={"city_id": city_id, "new_name": name},
            status="queued",
        )

        # Atualiza o nome no snapshot na hora (otimista)
        try:
            snap = AccountSnapshot.objects.get(game_account=ga)
            cities = snap.cities or []
            for c in cities:
                if isinstance(c, dict) and str(c.get("id") or "") == city_id:
                    c["name"] = name
            snap.cities = cities
            snap.save(update_fields=["cities", "updated_at"])
        except AccountSnapshot.DoesNotExist:
            pass

        return self._toast(f"Renomeando cidade para '{name}'...", "success")

    @staticmethod
    def _toast(message: str, toast_type: str = "info") -> HttpResponse:
        resp = HttpResponse(status=204)
        resp["HX-Trigger"] = json.dumps({"toast": {"type": toast_type, "message": message}})
        return resp
