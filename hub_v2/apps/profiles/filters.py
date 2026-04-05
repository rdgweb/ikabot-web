"""
django-filter FilterSet for Profile list view.
"""

import django_filters

from .models import Profile


class ProfileFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_expr="icontains", label="Nome")
    action_code = django_filters.NumberFilter(label="Código da Ação")
    enabled = django_filters.BooleanFilter(label="Ativo")

    class Meta:
        model = Profile
        fields = ["name", "action_code", "enabled"]
