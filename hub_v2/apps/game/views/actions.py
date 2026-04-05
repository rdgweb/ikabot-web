"""
View: Action catalog — lists all game action types grouped by category.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from core.contracts import get_actions_for_ui


class ActionCatalogView(LoginRequiredMixin, TemplateView):
    template_name = "game/action_catalog.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["action_groups"] = get_actions_for_ui()
        return ctx
