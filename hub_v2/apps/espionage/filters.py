import django_filters

from apps.accounts.models import GameAccount

from .models import SpyReport


class SpyReportFilter(django_filters.FilterSet):
    game_account = django_filters.ModelChoiceFilter(
        queryset=GameAccount.objects.order_by("name"),
        label="Subconta",
        empty_label="Todas as subcontas",
    )
    target_owner = django_filters.CharFilter(
        lookup_expr="icontains",
        label="Dono da cidade alvo",
    )
    status = django_filters.CharFilter(
        lookup_expr="icontains",
        label="Status",
    )

    class Meta:
        model = SpyReport
        fields = ["game_account", "target_owner", "status"]
