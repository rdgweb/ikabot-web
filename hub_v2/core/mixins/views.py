"""
Reusable view mixins for HTMX partial rendering, filtering, and sorting.
"""

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import ListView
from django_filters.views import FilterView


class StaffRequiredMixin(UserPassesTestMixin):
    """Restrict access to the platform team."""

    def test_func(self):
        from core.access import platform_admin
        return platform_admin(self.request.user)


class OperatorRequiredMixin(UserPassesTestMixin):
    """Use the current organization role, or the platform team."""

    def test_func(self):
        from core.access import operator
        return operator(self.request.user, getattr(self.request, "organization", None))


class HtmxPartialMixin:
    """
    If the request is from HTMX, render partial_template_name.
    Otherwise render the full template_name.
    """

    partial_template_name: str | None = None

    def get_template_names(self):
        # Only render partial for non-boosted HTMX requests (e.g., table filters)
        # Boosted navigation (hx-boost) should render the full page; the
        # HtmxBoostMiddleware strips it down to #page-content afterwards.
        htmx = getattr(self.request, "htmx", None)
        if self.partial_template_name and htmx and not htmx.boosted:
            return [self.partial_template_name]
        return super().get_template_names()


class FilterSortListView(LoginRequiredMixin, HtmxPartialMixin, FilterView):
    """
    ListView with filtering (django-filter), sorting, pagination,
    and HTMX partial swap support.

    Set these on subclasses:
        model = MyModel
        filterset_class = MyFilter
        template_name = "myapp/list.html"
        partial_template_name = "myapp/partials/table.html"
        paginate_by = 25
        ordering_fields = ["name", "created_at"]
        default_ordering = "-created_at"
    """

    paginate_by = 25
    ordering_fields: list[str] = []
    default_ordering: str = "-created_at"

    def get_ordering(self):
        sort = self.request.GET.get("sort")
        order = self.request.GET.get("order", "asc")
        if sort and sort in self.ordering_fields:
            return f"-{sort}" if order == "desc" else sort
        return self.default_ordering

    def get(self, request, *args, **kwargs):
        """Override get to apply ordering after filtering."""
        filterset_class = self.get_filterset_class()
        self.filterset = self.get_filterset(filterset_class)

        if not self.filterset.is_bound or self.filterset.is_valid() or not self.get_strict():
            self.object_list = self.filterset.qs
        else:
            self.object_list = self.filterset.queryset.none()

        # Apply ordering
        ordering = self.get_ordering()
        if ordering:
            if isinstance(ordering, str):
                self.object_list = self.object_list.order_by(ordering)
            elif isinstance(ordering, (list, tuple)):
                self.object_list = self.object_list.order_by(*ordering)

        context = self.get_context_data(
            filter=self.filterset,
            object_list=self.object_list,
        )
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["current_sort"] = self.request.GET.get("sort", "")
        context["current_order"] = self.request.GET.get("order", "asc")
        context["ordering_fields"] = self.ordering_fields
        query = self.request.GET.copy()
        query.pop("page", None)
        context["querystring_without_page"] = query.urlencode()
        return context
