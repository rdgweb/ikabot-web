"""
Dashboard: the first page of the hub (N-64 / N-102).

What needs attention, the empire in numbers, what is happening and what comes next,
the health of the automation, one line per account and a compact system footer.
The data comes from apps.dashboard.services.overview; the page refreshes itself every minute.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.urls import reverse
from django.views.generic import TemplateView

from .services.overview import dashboard_data


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/index.html"
    with_first_use = True              # N-116: the next first-use step on top, until all are done

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["dash"] = dashboard_data()
        if self.with_first_use:
            from apps.settings_app.first_steps import first_use

            steps = first_use()
            ctx["first_use_next"] = next((step for step in steps if not step["done"]), None)
            ctx["first_use_done"] = sum(1 for step in steps if step["done"])
            ctx["first_use_total"] = len(steps)
        return ctx


class DashboardLiveView(DashboardView):
    """GET /dashboard/live/ - only the blocks, for the refresh of the open page."""

    template_name = "dashboard/partials/live.html"
    with_first_use = False

    def handle_no_permission(self):
        # the session ended with the page open: send the browser to the login, not the login into a block
        return HttpResponse(status=204, headers={"HX-Redirect": reverse("login")})
