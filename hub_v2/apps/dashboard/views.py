"""
Dashboard views — real system-state overview: workflow health, agent/node
health, notes queue, and usage telemetry.

N-64: the original dashboard (nos online, contas ativas, 10 jobs recentes
crus) predates workflows, presets, notes and telemetry — it no longer
reflects what the system actually does. This rebuilds the content (same
cards/table/sidebar shell) around what the rest of the app already models:
Workflow.status, the Notes queue, and the public telemetry pipeline.
"""

from datetime import timedelta

import requests
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.utils import timezone
from django.views.generic import TemplateView

from apps.accounts.models import Account, GameAccount, Node
from apps.jobs.models import Job, Workflow
from apps.notes.models import Note

# Fixed palette so a status/world keeps the same color across renders.
WORKFLOW_STATUS_COLORS = {
    "active": "#5f8c4e",
    "problem": "#c0392b",
    "waiting": "#c99c4e",
    "paused": "#8a8a8a",
    "draft": "#b8a888",
    "finished": "#4f86b9",
    "cancelled": "#b0a89a",
}
NOTE_STATUS_COLORS = {
    "open": "#b0a89a",
    "authorized": "#4f86b9",
    "doing": "#c99c4e",
    "pending_approval": "#8e6fb0",
    "done": "#5f8c4e",
    "archived": "#8a8a8a",
}
WORLD_CHART_COLORS = [
    "#4f86b9", "#5f8c4e", "#c99c4e", "#c0392b", "#8e6fb0",
    "#3f9c9c", "#b06fa0", "#7a9e4e", "#a97b4f", "#6f7fb0",
]


def _donut_segments(counts: list[tuple[str, int, str]]) -> list[dict]:
    """Build stroke-dasharray/offset data for a pure-SVG donut chart.

    counts: list of (label, count, color). Returns segments ready for the
    template, each with a percent and cumulative offset around a circle of
    circumference 100 (r = 100/(2*pi)).
    """
    total = sum(count for _, count, _ in counts)
    segments = []
    offset = 0.0
    for label, count, color in counts:
        if count <= 0:
            continue
        percent = (count / total * 100) if total else 0
        # Formatted as plain (locale-independent) strings — SVG attributes
        # require a "." decimal separator, but Django's number rendering in
        # templates uses the active locale's separator (pt-BR gives "50,0"),
        # which silently breaks stroke-dasharray/stroke-dashoffset.
        segments.append({
            "label": label,
            "count": count,
            "color": color,
            "percent": f"{percent:.2f}",
            "remainder": f"{100 - percent:.2f}",
            "dash_offset": f"{-offset:.2f}",
        })
        offset += percent
    return {"segments": segments, "total": total}


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/index.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        now = timezone.now()
        last_24h = now - timedelta(hours=24)

        # --- Top stat cards -------------------------------------------------
        nodes = list(Node.objects.filter(active=True))
        ctx["total_nodes"] = len(nodes)
        ctx["online_nodes"] = sum(1 for n in nodes if n.is_online)
        ctx["total_accounts"] = Account.objects.filter(active=True).count()

        live_workflows = Workflow.objects.filter(archived_at__isnull=True)
        ctx["workflows_active"] = live_workflows.filter(status__in=("active", "waiting")).count()
        ctx["workflows_problem"] = live_workflows.filter(status="problem").count()

        notes_open_queue = Note.objects.filter(status__in=("open", "authorized")).count()
        notes_doing = Note.objects.filter(status="doing").count()
        ctx["notes_queue_count"] = notes_open_queue
        ctx["notes_doing_count"] = notes_doing

        ctx["jobs_error_24h"] = Job.objects.filter(status="error", created_at__gte=last_24h).count()

        # --- Workflows com problema (substitui o feed cru de jobs) ---------
        ctx["problem_workflows"] = list(
            live_workflows.filter(status="problem")
            .select_related("account", "game_account", "node")
            .order_by("-last_error_at")[:8]
        )
        status_counts = live_workflows.values("status").annotate(n=Count("id"))
        status_map = {row["status"]: row["n"] for row in status_counts}
        ctx["workflow_chart"] = _donut_segments([
            (label, status_map.get(key, 0), WORKFLOW_STATUS_COLORS.get(key, "#999"))
            for key, label in Workflow.STATUS_CHOICES
        ])

        # --- Saude de agents/nodes -------------------------------------------
        ctx["nodes"] = nodes

        # --- Fila de notas -----------------------------------------------------
        note_status_counts = Note.objects.values("status").annotate(n=Count("id"))
        note_status_map = {row["status"]: row["n"] for row in note_status_counts}
        ctx["notes_chart"] = _donut_segments([
            (label, note_status_map.get(key, 0), NOTE_STATUS_COLORS.get(key, "#999"))
            for key, label in Note.STATUS_CHOICES
            if key not in ("done", "archived")
        ])
        ctx["notes_doing_list"] = list(
            Note.objects.filter(status="doing").order_by("-claimed_at")[:6]
        )

        # --- Telemetria / uso ----------------------------------------------
        world_counts = (
            GameAccount.objects.filter(account__active=True)
            .values("server_id")
            .annotate(n=Count("id"))
            .order_by("-n")[:8]
        )
        ctx["accounts_by_world"] = _donut_segments([
            (row["server_id"], row["n"], WORLD_CHART_COLORS[i % len(WORLD_CHART_COLORS)])
            for i, row in enumerate(world_counts)
        ])
        ctx["telemetry"] = self._fetch_public_telemetry()

        return ctx

    @staticmethod
    def _fetch_public_telemetry() -> dict | None:
        """Best-effort fetch of the public, aggregate telemetry summary.

        The telemetry service is a separate deployment — never let it being
        slow or down break the dashboard.
        """
        url = getattr(settings, "TELEMETRY_PUBLIC_STATS_URL", "https://telemetry.rdgh.com.br/v1/stats/public")
        try:
            resp = requests.get(url, timeout=2.0)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None
