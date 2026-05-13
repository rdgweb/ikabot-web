import django_filters

from apps.accounts.models import GameAccount

from .models import SpyReport

MISSION_CHOICES = [
    (1,  "Enviar espião"),
    (3,  "Nível de pesquisa"),
    (5,  "Inspecionar armazém"),
    (6,  "Guarnição militar"),
    (7,  "Tropas e frotas"),
    (8,  "Chamar espião"),
    (10, "Observar comunicação"),
    (21, "Ver estado"),
    (23, "Produção militar"),
    (24, "Cargo na aliança"),
    (25, "Forma de governo"),
    (26, "Invenções"),
    (27, "Colônias"),
]


class SpyReportFilter(django_filters.FilterSet):
    game_account = django_filters.ModelChoiceFilter(
        queryset=GameAccount.objects.order_by("name"),
        label="Subconta",
        empty_label="Todas as subcontas",
    )
    target_owner = django_filters.CharFilter(
        lookup_expr="icontains",
        label="Alvo",
    )
    mission_id = django_filters.ChoiceFilter(
        choices=MISSION_CHOICES,
        label="Tipo de missão",
        empty_label="Todas as missões",
    )
    result = django_filters.CharFilter(
        field_name="result_status",
        lookup_expr="icontains",
        label="Resultado",
    )

    class Meta:
        model = SpyReport
        fields = ["game_account", "target_owner", "mission_id", "result"]
