"""
Template context processors for global template data.
"""

from django.conf import settings


def nav_context(request):
    """Provide sidebar navigation context filtered by user permissions."""
    user = getattr(request, "user", None)
    # Cache permission checks on the user object to avoid repeated DB queries
    # within the same request (context processor runs once per render).
    if not hasattr(user, "_perm_is_admin"):
        user._perm_is_admin = _is_admin(user)
        user._perm_is_operator = _is_operator(user)
    is_admin = user._perm_is_admin
    is_operator = user._perm_is_operator

    all_sections = [
        {
            "label": "Operacional",
            "items": [
                {"name": "Dashboard", "url": "/", "icon": "bi-speedometer2"},
                {"name": "Painel do Jogo", "url": "/game/", "icon": "bi-joystick"},
                {"name": "Ações do Jogo", "url": "/game/actions/", "icon": "bi-controller"},
                {"name": "Construções", "url": "/game/construction/", "icon": "bi-building"},
                {"name": "Mercado Interno", "url": "/market/", "icon": "bi-shop"},
                {"name": "Fila de Jobs", "url": "/jobs/", "icon": "bi-list-task"},
            ],
        },
        {
            "label": "Administrativo",
            "perm": "operator",
            "items": [
                {"name": "Nós & Agents", "url": "/accounts/nodes/", "icon": "bi-hdd-network"},
                {"name": "Contas do Jogo", "url": "/accounts/", "icon": "bi-person-badge"},
                {"name": "Presets", "url": "/profiles/", "icon": "bi-collection"},
                {"name": "Proxy", "url": "/proxy/", "icon": "bi-shield-lock"},
                {"name": "Telegram", "url": "/telegram/", "icon": "bi-telegram"},
                {"name": "Mensagens", "url": "/telegram/audit/", "icon": "bi-chat-dots"},
                {"name": "Captcha", "url": "/captcha/", "icon": "bi-robot"},
                {"name": "Configurações", "url": "/settings/", "icon": "bi-gear", "perm": "admin"},
                {"name": "Usuários", "url": "/users/", "icon": "bi-people", "perm": "admin"},
            ],
        },
    ]

    # Filter sections and items by permission
    filtered = []
    for section in all_sections:
        section_perm = section.get("perm")
        if section_perm == "admin" and not is_admin:
            continue
        if section_perm == "operator" and not is_operator:
            continue

        items = [
            item for item in section["items"]
            if not item.get("perm")
            or (item["perm"] == "admin" and is_admin)
            or (item["perm"] == "operator" and is_operator)
        ]
        if items:
            filtered.append({"label": section["label"], "items": items})

    return {
        "NAV_SECTIONS": filtered,
        "user_is_admin": is_admin,
        "user_is_operator": is_operator,
    }


def _is_admin(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser or user.is_staff:
        return True
    return user.groups.filter(name="admin").exists()


def _is_operator(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser or user.is_staff:
        return True
    return user.groups.filter(name__in=["admin", "operator"]).exists()


def hub_version(request):
    """Provide hub version in templates."""
    return {"HUB_VERSION": settings.VERSION}


def htmx_context(request):
    """Provide HTMX-aware base template selection.

    When navigating via hx-boost, only the #page-content needs to be
    swapped — not the entire HTML document. Templates use::

        {% extends base_template %}

    instead of ``{% extends "base.html" %}``.
    """
    is_htmx = request.headers.get("HX-Request") == "true"
    is_boosted = request.headers.get("HX-Boosted") == "true"

    if is_htmx and is_boosted:
        base = "base_partial.html"
    else:
        base = "base.html"

    return {"base_template": base, "is_htmx": is_htmx}
