from django.contrib import messages
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.utils.dateparse import parse_datetime
from django.views import View

from apps.audit.services import record
from . import usage
from .retention_views import StaffOnly


class OrganizationUsageView(StaffOnly, View):
    def get(self, request):
        data = usage.snapshot()
        if data:
            data["at"] = parse_datetime(data["finished_at"])
            for point in data.get("history", []):
                point["date"] = parse_datetime(point["at"])
        return render(request, "settings_app/usage.html", {
            "report": data, "collecting": usage.collecting(), "collection_error": cache.get(usage.ERROR_KEY),
        })

    def post(self, request):
        if usage.start():
            record("usage.requested", user=request.user, details={"scope": "installation"})
            messages.success(request, "Coleta iniciada. A página mostrará o resultado quando terminar.")
        else:
            messages.info(request, "Já existe uma coleta em andamento.")
        return redirect("settings_app:usage")
