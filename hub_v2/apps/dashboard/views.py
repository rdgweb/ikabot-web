"""
Dashboard views — overview stats, recent activity, and node health.
"""

from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.views.generic import TemplateView

from apps.accounts.models import Account, Node
from apps.jobs.models import Job


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/index.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        now = timezone.now()
        last_24h = now - timedelta(hours=24)

        # Stats
        ctx["total_nodes"] = Node.objects.filter(active=True).count()
        ctx["online_nodes"] = sum(
            1 for n in Node.objects.filter(active=True) if n.is_online
        )
        ctx["total_accounts"] = Account.objects.filter(active=True).count()
        ctx["total_jobs"] = Job.objects.count()

        # Job stats
        ctx["jobs_running"] = Job.objects.filter(status="running").count()
        ctx["jobs_queued"] = Job.objects.filter(status="queued").count()
        ctx["jobs_error_24h"] = Job.objects.filter(
            status="error", created_at__gte=last_24h
        ).count()
        ctx["jobs_finished_24h"] = Job.objects.filter(
            status="finished", created_at__gte=last_24h
        ).count()

        # Recent jobs
        ctx["recent_jobs"] = (
            Job.objects.select_related("account", "game_account", "node")
            .order_by("-created_at")[:10]
        )

        # Nodes with status
        ctx["nodes"] = Node.objects.filter(active=True).prefetch_related("accounts")

        return ctx
