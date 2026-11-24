"""N-187: the Maintenance page (administrators only)."""
from django import forms
from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.views import View

from . import maintenance
from .retention_views import StaffOnly

WEEKDAYS = [(0, "segunda"), (1, "terça"), (2, "quarta"), (3, "quinta"), (4, "sexta"), (5, "sábado"), (6, "domingo")]


LABELS = {"ok": "feito", "failed": "falhou", "skipped": "pulado", "running": "rodando"}


def _shown(run):
    if not run:
        return None
    from datetime import datetime

    from django.utils import timezone

    when = ""
    try:
        when = timezone.localtime(datetime.fromisoformat(run.get("started_at") or "")).strftime("%d/%m %H:%M")
    except ValueError:
        pass
    return {**run, "label": LABELS.get(run.get("status"), run.get("status")), "when": when}


class MaintenanceForm(forms.Form):
    backup_enabled = forms.BooleanField(required=False, label="Backup diário ligado")
    backup_hour = forms.IntegerField(min_value=0, max_value=23, label="Hora do backup")
    backup_keep = forms.IntegerField(min_value=1, max_value=30, label="Backups guardados")
    optimize_enabled = forms.BooleanField(required=False, label="Otimização semanal ligada")
    optimize_weekday = forms.TypedChoiceField(choices=WEEKDAYS, coerce=int, label="Dia da otimização")
    optimize_hour = forms.IntegerField(min_value=0, max_value=23, label="Hora da otimização")
    optimize_min_free_mb = forms.IntegerField(min_value=10, label="Otimizar tabela com pelo menos (MB livres)")
    disk_alert_pct = forms.IntegerField(min_value=50, max_value=99, label="Alerta de disco acima de (%)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if not isinstance(field, forms.BooleanField):
                field.widget.attrs["class"] = "form-input"


class MaintenanceView(StaffOnly, View):
    def get(self, request):
        config = maintenance.get_config()
        disk = maintenance.disk()
        database = maintenance.database()
        files = maintenance.backups()
        history = [_shown(r) for r in list(reversed(maintenance.runs()))[:12]]
        return render(request, "settings_app/maintenance.html", {
            "form": MaintenanceForm(initial=config), "config": config, "disk": disk,
            "disk_alert": bool(disk and disk["pct"] >= config["disk_alert_pct"]),
            "database": database, "tables": database["tables"][:8], "backups": files,
            "backups_size": sum(b["size"] for b in files), "history": history, "busy": maintenance.busy(),
            "last_backup": _shown(maintenance.last_run("backup")), "last_optimize": _shown(maintenance.last_run("optimize")),
            "backup_dir_ok": maintenance.os.path.isdir(maintenance.BACKUP_DIR),
            "base_template": "base_partial.html" if request.headers.get("HX-Request") == "true" else "base.html",
        })

    def post(self, request):
        action = request.POST.get("action")
        if action in ("backup", "optimize") and request.POST.get("scope") != "installation":
            messages.error(request, "Confirme que a operação abrange todas as organizações desta instalação.")
            return redirect("settings_app:maintenance")
        if action == "save":
            form = MaintenanceForm(request.POST)
            if form.is_valid():
                maintenance.save_config(form.cleaned_data)
                messages.success(request, "Manutenção salva.")
            else:
                messages.error(request, "Confira os valores: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in form.errors.items()))
        elif action in ("backup", "optimize"):
            if maintenance.start(action):
                from apps.audit.services import record

                record("maintenance.requested", user=request.user,
                       details={"operation": action, "scope": "installation"})
                messages.success(request, "Backup iniciado no servidor." if action == "backup" else "Otimização iniciada no servidor.")
            else:
                messages.error(request, "Já há uma manutenção rodando; espere ela terminar.")
        return redirect("settings_app:maintenance")


class MaintenanceBackupDownloadView(StaffOnly, View):
    def get(self, request, name):
        path = maintenance.backup_path(name)
        if not path:
            raise Http404
        from apps.audit.services import record

        record("maintenance.backup_download", user=request.user, details={"file": name, "scope": "installation"})
        return FileResponse(open(path, "rb"), as_attachment=True, filename=name)
