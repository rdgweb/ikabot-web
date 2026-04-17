import django_filters

from apps.accounts.models import GameAccount

from .models import DiplomacyMessage


class DiplomacyMessageFilter(django_filters.FilterSet):
    game_account = django_filters.ModelChoiceFilter(
        queryset=GameAccount.objects.order_by("name"),
        label="Subconta",
        empty_label="Todas as subcontas",
    )
    status = django_filters.ChoiceFilter(
        choices=[("", "Todos os status")] + DiplomacyMessage.STATUS_CHOICES,
        label="Status",
        empty_label=None,
    )
    sender = django_filters.CharFilter(
        lookup_expr="icontains",
        label="Remetente",
    )

    class Meta:
        model = DiplomacyMessage
        fields = ["game_account", "status", "sender"]
