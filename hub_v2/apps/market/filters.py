"""
django-filter FilterSets for Market order list views.
"""

import django_filters

from .models import InternalMarketOrder


class MarketOrderFilter(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(
        choices=InternalMarketOrder.STATUS_CHOICES, label="Status"
    )
    resource_idx = django_filters.ChoiceFilter(
        choices=InternalMarketOrder.RESOURCE_CHOICES, label="Recurso"
    )

    class Meta:
        model = InternalMarketOrder
        fields = ["status", "resource_idx"]
