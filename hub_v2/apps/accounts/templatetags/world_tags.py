"""N-241: {{ ga.server_id|world }} -> "Gigas · s78-br"; {{ ga.server_id|world_name }} -> "Gigas" (or "")."""
from django import template

from apps.accounts import world_names

register = template.Library()


@register.filter(name="world")
def world(server_id):
    return world_names.world_label(server_id)


@register.filter(name="world_name")
def world_name(server_id):
    return world_names.world_name(server_id)
