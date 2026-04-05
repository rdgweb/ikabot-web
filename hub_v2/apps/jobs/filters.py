"""
django-filter FilterSets for Job list views.
"""

import django_filters

from apps.accounts.models import Account, GameAccount, Node

from .models import Job


class JobFilter(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(choices=Job.STATUS_CHOICES, label="Status")
    account = django_filters.ModelChoiceFilter(queryset=Account.objects.all(), label="Conta")
    game_account = django_filters.ModelChoiceFilter(queryset=GameAccount.objects.filter(active=True), label="Subconta")
    node = django_filters.ModelChoiceFilter(queryset=Node.objects.all(), label="No")
    action_code = django_filters.NumberFilter(label="Codigo da Acao")
    city = django_filters.CharFilter(label="Cidade", method="filter_city")

    def filter_city(self, queryset, name, value):
        token = (value or "").strip()
        if not token:
            return queryset
        return queryset.filter(inputs_json__icontains=token)

    class Meta:
        model = Job
        fields = ["status", "account", "game_account", "node", "action_code", "city"]
