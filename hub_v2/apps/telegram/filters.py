"""
django-filter FilterSets for Telegram message audit views.
"""

import django_filters

from apps.accounts.models import Account, GameAccount

from .models import MessageAudit


class MessageAuditFilter(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(
        choices=MessageAudit.STATUS_CHOICES, label="Status"
    )
    account = django_filters.ModelChoiceFilter(
        queryset=Account.objects.all(), label="Conta"
    )
    game_account = django_filters.ModelChoiceFilter(
        queryset=GameAccount.objects.all(), label="Subconta"
    )
    channel = django_filters.CharFilter(lookup_expr="exact", label="Canal")

    class Meta:
        model = MessageAudit
        fields = ["status", "account", "game_account", "channel"]
