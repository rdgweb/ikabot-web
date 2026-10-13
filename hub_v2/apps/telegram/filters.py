"""
django-filter FilterSets for Telegram message audit views.
"""

import django_filters

from apps.accounts.models import Account, GameAccount

from .constants import EVENT_TYPES
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
    # N-104: the kind of notice, by its name (the history stores it in "channel")
    channel = django_filters.ChoiceFilter(choices=[(event["key"], event["label"]) for event in EVENT_TYPES], label="Tipo de aviso")

    class Meta:
        model = MessageAudit
        fields = ["status", "account", "game_account", "channel"]
