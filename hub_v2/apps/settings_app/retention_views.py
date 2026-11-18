import json

from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from .models import AppSetting, RetentionRun
from .retention import (DEFAULT_POLICY, POLICY_KEY, get_policy, pause, preview, process_batch, resume, resume_stalled,
                        runner_active, started_on_server)


class RetentionPolicyForm(forms.Form):
    enabled = forms.BooleanField(required=False, label="Permitir execução da retenção")
    snapshot_days = forms.IntegerField(min_value=1, max_value=3650, label="Detalhes dos snapshots (dias)")
    job_days = forms.IntegerField(min_value=1, max_value=3650, label="Resultados dos jobs arquivados (dias)")
    log_days = forms.IntegerField(min_value=1, max_value=3650, label="Logs dos jobs arquivados (dias)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name != "enabled":
                field.widget.attrs["class"] = "form-input"


class StaffOnly(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_superuser or self.request.user.is_staff


class RetentionPolicyView(StaffOnly, View):
    def post(self, request):
        form = RetentionPolicyForm(request.POST)
        if not form.is_valid():
            return render(request, "settings_app/retention_form_error.html", {"form": form}, status=400)
        with transaction.atomic():
            setting, _ = AppSetting.objects.get_or_create(pk=POLICY_KEY, defaults={"value": json.dumps(DEFAULT_POLICY)})
            setting = AppSetting.objects.select_for_update().get(pk=setting.pk)
            setting.value = json.dumps(form.cleaned_data)
            setting.save()
        messages.success(request, "Política salva. Nenhum dado foi removido; gere a prévia para executar.")
        return redirect("/settings/#retencao")


class RetentionPreviewView(StaffOnly, View):
    def post(self, request):
        run = preview(request.user)
        return redirect("settings_app:retention-run", pk=run.pk)


class RetentionRunView(StaffOnly, View):
    """N-196: the server runs the batches; this page starts, pauses or resumes the run and shows how far it got."""

    def _page(self, request, run):
        on_server = started_on_server(run)
        if on_server and not runner_active(run.pk):
            resume_stalled()                    # nobody is running it (hub restarted): go on now
        return render(request, "settings_app/retention_run.html", {
            "run": run, "on_server": on_server, "error": (run.processed or {}).get("error"),
            "base_template": "base_partial.html" if request.headers.get("HX-Request") == "true" else "base.html",
        })

    def get(self, request, pk):
        return self._page(request, get_object_or_404(RetentionRun, pk=pk, user=request.user))

    def post(self, request, pk):
        get_object_or_404(RetentionRun, pk=pk, user=request.user)
        action = request.POST.get("action")
        if action not in ("start", "pause", "resume", "next"):
            return HttpResponseBadRequest("Ação inválida.")
        try:
            if action == "start":
                run = process_batch(pk, request.user, start=True, backup=request.POST.get("backup") == "yes")
                if run.status == "running":
                    run = resume(run.pk)
            elif action == "pause":
                run = pause(pk)
            else:
                run = resume(pk)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("settings_app:retention-run", pk=pk)
        return redirect("settings_app:retention-run", pk=run.pk)
