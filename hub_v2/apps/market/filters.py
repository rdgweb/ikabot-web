"""
django-filter FilterSets for Market order list views.
"""

import django_filters

from apps.accounts.models import GameAccount

from .models import InternalMarketOrder


class MarketOrderFilter(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(
        choices=InternalMarketOrder.STATUS_CHOICES, label="Status"
    )
    resource_idx = django_filters.ChoiceFilter(
        choices=InternalMarketOrder.RESOURCE_CHOICES, label="Recurso"
    )

    # N-111: the purchases and the sales of one account
    buyer = django_filters.ModelChoiceFilter(field_name="buyer_game_account", queryset=GameAccount.objects.all(), label="Comprador")
    seller = django_filters.ModelChoiceFilter(field_name="seller_game_account", queryset=GameAccount.objects.all(), label="Vendedor")

    class Meta:
        model = InternalMarketOrder
        fields = ["status", "resource_idx", "buyer", "seller"]
