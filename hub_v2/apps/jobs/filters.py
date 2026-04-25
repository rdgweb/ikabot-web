"""
django-filter FilterSets for job and workflow list views.
"""

import django_filters
from django.db.models import Exists, OuterRef, Q

from apps.accounts.models import Account, GameAccount, Node
from core.actions.constants import CATEGORY_META
from core.contracts import ACTION_CATALOG

from .models import Job, Workflow


class JobFilter(django_filters.FilterSet):
    WORKFLOW_STATE_CHOICES = [
        ("active", "Ativos"),
        ("problem", "Com problema"),
        ("done", "Concluidos"),
        ("stopped", "Cancelados"),
    ]
    WORKFLOW_TYPE_CHOICES = [
        ("construction", "Construcao"),
        ("transport", "Transporte"),
        ("grouped", "Agrupados"),
        ("single", "Pontuais"),
    ]

    status = django_filters.ChoiceFilter(choices=Job.STATUS_CHOICES, label="Status")
    account = django_filters.ModelChoiceFilter(queryset=Account.objects.all(), label="Conta")
    game_account = django_filters.ModelChoiceFilter(queryset=GameAccount.objects.filter(active=True), label="Subconta")
    node = django_filters.ModelChoiceFilter(queryset=Node.objects.all(), label="No")
    action_code = django_filters.NumberFilter(label="Codigo da Acao")
    city = django_filters.CharFilter(label="Cidade", method="filter_city")
    category = django_filters.ChoiceFilter(label="Categoria", method="filter_category")
    workflow_state = django_filters.ChoiceFilter(
        choices=WORKFLOW_STATE_CHOICES,
        label="Estado operacional",
        method="filter_workflow_state",
    )
    workflow_type = django_filters.ChoiceFilter(
        choices=WORKFLOW_TYPE_CHOICES,
        label="Tipo de workflow",
        method="filter_workflow_type",
    )
    search = django_filters.CharFilter(label="Busca", method="filter_search")

    @property
    def form(self):
        form = super().form
        form.fields["category"].choices = [("", "Todas"), *self.category_choices()]
        return form

    @staticmethod
    def category_choices():
        seen = {}
        for meta in ACTION_CATALOG.values():
            key = str(meta.get("category") or "").strip()
            if key and key not in seen:
                seen[key] = CATEGORY_META.get(key, {}).get("label", key.title())
        return sorted(seen.items(), key=lambda item: item[1].lower())

    def filter_city(self, queryset, name, value):
        token = (value or "").strip()
        if not token:
            return queryset
        return queryset.filter(inputs_json__icontains=token)

    def filter_category(self, queryset, name, value):
        category = (value or "").strip()
        if not category:
            return queryset
        action_codes = [
            code
            for code, meta in ACTION_CATALOG.items()
            if str(meta.get("category") or "").strip() == category
        ]
        if not action_codes:
            return queryset.none()
        return queryset.filter(action_code__in=action_codes)

    def filter_workflow_state(self, queryset, name, value):
        state = (value or "").strip()
        if not state:
            return queryset

        active_descendants = Job.objects.filter(
            root_job_id=OuterRef("pk"),
            status__in=["queued", "running", "scheduled"],
        )
        queryset = queryset.annotate(has_active_descendant=Exists(active_descendants))

        if state == "active":
            return queryset.filter(
                Q(status__in=["queued", "running", "scheduled"]) | Q(has_active_descendant=True)
            )
        if state == "problem":
            return queryset.filter(status="error", has_active_descendant=False)
        if state == "done":
            return queryset.filter(status="finished", has_active_descendant=False)
        if state == "stopped":
            return queryset.filter(status="cancelled", has_active_descendant=False)
        return queryset

    def filter_workflow_type(self, queryset, name, value):
        workflow_type = (value or "").strip()
        if not workflow_type:
            return queryset
        if workflow_type == "construction":
            return queryset.filter(action_code=1002)
        if workflow_type == "transport":
            return queryset.filter(action_code=2)
        grouped_codes = [
            code
            for code, meta in ACTION_CATALOG.items()
            if meta.get("recurring") or meta.get("long_running")
        ]
        if workflow_type == "grouped":
            return queryset.filter(Q(action_code__in=[1002, 2]) | Q(action_code__in=grouped_codes))
        if workflow_type == "single":
            return queryset.exclude(action_code__in=grouped_codes + [1002, 2])
        return queryset

    def filter_search(self, queryset, name, value):
        token = (value or "").strip()
        if not token:
            return queryset
        return queryset.filter(
            Q(account__label__icontains=token)
            | Q(game_account__name__icontains=token)
            | Q(game_account__server_id__icontains=token)
            | Q(node__name__icontains=token)
            | Q(inputs_json__icontains=token)
        )

    class Meta:
        model = Job
        fields = [
            "status",
            "account",
            "game_account",
            "node",
            "action_code",
            "city",
            "category",
            "workflow_state",
            "workflow_type",
            "search",
        ]


class WorkflowFilter(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(choices=Workflow.STATUS_CHOICES, label="Status")
    account = django_filters.ModelChoiceFilter(queryset=Account.objects.all(), label="Conta")
    game_account = django_filters.ModelChoiceFilter(queryset=GameAccount.objects.filter(active=True), label="Subconta")
    node = django_filters.ModelChoiceFilter(queryset=Node.objects.all(), label="No")
    category = django_filters.ChoiceFilter(label="Categoria")
    workflow_type = django_filters.CharFilter(label="Tipo", lookup_expr="icontains")
    search = django_filters.CharFilter(label="Busca", method="filter_search")
    city = django_filters.CharFilter(label="Cidade", method="filter_city")

    @property
    def form(self):
        form = super().form
        form.fields["category"].choices = [("", "Todas"), *JobFilter.category_choices()]
        return form

    def filter_search(self, queryset, name, value):
        token = (value or "").strip()
        if not token:
            return queryset
        return queryset.filter(
            Q(account__label__icontains=token)
            | Q(game_account__name__icontains=token)
            | Q(game_account__server_id__icontains=token)
            | Q(node__name__icontains=token)
            | Q(workflow_type__icontains=token)
            | Q(scope_json__icontains=token)
            | Q(config_json__icontains=token)
        )

    def filter_city(self, queryset, name, value):
        token = (value or "").strip()
        if not token:
            return queryset
        return queryset.filter(scope_json__icontains=token)

    class Meta:
        model = Workflow
        fields = [
            "status",
            "account",
            "game_account",
            "node",
            "category",
            "workflow_type",
            "city",
            "search",
        ]
