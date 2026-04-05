"""
django-filter FilterSets for Node and Account list views.
"""

import django_filters

from .models import Node, Account


class NodeFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_expr="icontains", label="Nome")
    active = django_filters.BooleanFilter(label="Ativo")

    class Meta:
        model = Node
        fields = ["name", "active"]


class AccountFilter(django_filters.FilterSet):
    label = django_filters.CharFilter(lookup_expr="icontains", label="Label")
    email = django_filters.CharFilter(lookup_expr="icontains", label="Email")
    node = django_filters.ModelChoiceFilter(queryset=Node.objects.all(), label="Nó")
    active = django_filters.BooleanFilter(label="Ativo")

    class Meta:
        model = Account
        fields = ["label", "email", "node", "active"]
