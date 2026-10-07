from django import template

from apps.accounts.avatars import gallery_for_ui

register = template.Library()


@register.simple_tag
def avatar_gallery():
    """Artes do jogo que podem ser a imagem de uma conta, agrupadas: {% avatar_gallery as groups %}."""
    return gallery_for_ui()
