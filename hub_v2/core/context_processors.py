"""Template context processors for global template data."""

from django.conf import settings


def nav_context(request):
    """Provide sidebar navigation context filtered by user permissions."""
    user = getattr(request, "user", None)
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
                {"name": "Inteligencia", "url": "/intel/players/", "icon": "bi-binoculars"},
                {"name": "Acoes do Jogo", "url": "/game/actions/", "icon": "bi-controller"},
                {"name": "Construcoes", "url": "/game/construction/", "icon": "bi-building"},
                {"name": "Mercado Interno", "url": "/market/", "icon": "bi-shop"},
                {"name": "Mercado Negro", "url": "/market/black-market/", "icon": "bi-shield-exclamation"},
                {"name": "Banco de Generais", "url": "/generais/", "icon": "bi-shield-fill-check"},
                {"name": "Diplomacia", "url": "/diplomacy/", "icon": "bi-envelope"},
                {"name": "Espionagem", "url": "/espionage/", "icon": "bi-binoculars"},
                {"name": "Fila de Jobs", "url": "/jobs/", "icon": "bi-list-task"},
            ],
        },
        {
            "label": "Administrativo",
            "perm": "operator",
            "items": [
                {"name": "Nos & Agents", "url": "/accounts/nodes/", "icon": "bi-hdd-network"},
                {"name": "Contas do Jogo", "url": "/accounts/", "icon": "bi-person-badge"},
                {"name": "Presets", "url": "/profiles/presets/", "icon": "bi-collection"},
                {"name": "Proxy", "url": "/proxy/", "icon": "bi-shield-lock"},
                {"name": "Telegram", "url": "/telegram/", "icon": "bi-telegram"},
                {"name": "Mensagens", "url": "/telegram/audit/", "icon": "bi-chat-dots"},
                {"name": "Captcha", "url": "/captcha/", "icon": "bi-robot"},
                {"name": "Notas", "url": "/notes/", "icon": "bi-card-checklist"},
                {"name": "Atualizacoes", "url": "/notes/atualizacoes/", "icon": "bi-journal-code"},
                {"name": "Configuracoes", "url": "/settings/", "icon": "bi-gear", "perm": "admin"},
                {"name": "Usuarios", "url": "/users/", "icon": "bi-people", "perm": "admin"},
            ],
        },
    ]

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
    """Provide HTMX-aware base template selection."""
    is_htmx = request.headers.get("HX-Request") == "true"
    is_boosted = request.headers.get("HX-Boosted") == "true"
    base = "base_partial.html" if is_htmx and is_boosted else "base.html"
    return {"base_template": base, "is_htmx": is_htmx}
