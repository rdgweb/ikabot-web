"""Template context processors for global template data."""

from django.conf import settings


def nav_context(request):
    """Provide sidebar navigation context filtered by user permissions."""
    user = getattr(request, "user", None)
    if not hasattr(user, "_perm_is_admin"):
        user._perm_is_admin = _is_admin(user)
        user._perm_is_operator = _is_operator(user)
        user._perm_is_staff = _is_staff(user)
        user._perm_is_owner = _is_owner(user)
    is_admin = user._perm_is_admin
    is_operator = user._perm_is_operator
    is_staff = user._perm_is_staff
    is_owner = user._perm_is_owner

    all_sections = [
        {
            "label": "Operacional",
            "items": [
                {"name": "Dashboard", "url": "/", "icon": "bi-speedometer2"},
                {"name": "Painel do Jogo", "url": "/game/", "icon": "bi-joystick"},
                {"name": "Inteligência", "url": "/intel/players/", "icon": "bi-binoculars"},
                {"name": "Ações do Jogo", "url": "/game/actions/", "icon": "bi-controller"},
                {"name": "Construções", "url": "/game/construction/", "icon": "bi-building"},
                {"name": "Mercado Interno", "url": "/market/", "icon": "bi-shop"},
                {"name": "Mercado Negro", "url": "/market/black-market/", "icon": "bi-shield-exclamation"},
                {"name": "Banco de Generais", "url": "/generais/", "icon": "bi-shield-fill-check"},
                {"name": "Diplomacia", "url": "/diplomacy/", "icon": "bi-envelope"},
                {"name": "Espionagem", "url": "/espionage/", "icon": "bi-binoculars"},
                {"name": "Combates", "url": "/combat/", "icon": "bi-shield-shaded"},
                {"name": "Fila de Jobs", "url": "/jobs/", "icon": "bi-list-task"},
            ],
        },
        {
            # N-106: what each organization sets for itself
            "label": "Minha organização",
            "items": [
                {"name": "Telegram", "url": "/organizacao/telegram/", "icon": "bi-telegram"},
                {"name": "Plano", "url": "/organizacao/plano/", "icon": "bi-credit-card"},
            ],
        },
        {
            "label": "Administrativo",
            "perm": "operator",
            "items": [
                {"name": "Nós & Agents", "url": "/accounts/nodes/", "icon": "bi-hdd-network"},
                {"name": "Contas do Jogo", "url": "/accounts/", "icon": "bi-person-badge"},
                {"name": "Presets", "url": "/profiles/presets/", "icon": "bi-collection"},
                {"name": "Proxy", "url": "/proxy/", "icon": "bi-shield-lock", "perm": "staff"},
                {"name": "Telegram", "url": "/telegram/", "icon": "bi-telegram", "perm": "staff"},
                {"name": "Mensagens", "url": "/telegram/audit/", "icon": "bi-chat-dots", "perm": "staff"},
                {"name": "Captcha", "url": "/captcha/", "icon": "bi-robot", "perm": "staff"},
                {"name": "Notas", "url": "/notes/", "icon": "bi-card-checklist", "perm": "staff"},
                {"name": "Atualizações", "url": "/notes/atualizacoes/", "icon": "bi-journal-code", "perm": "staff"},
                {"name": "Atividades", "url": "/atividades/", "icon": "bi-clock-history", "perm": "owner"},
                {"name": "Configurações", "url": "/settings/", "icon": "bi-gear", "perm": "staff"},
                {"name": "Usuários", "url": "/users/", "icon": "bi-people", "perm": "staff"},
                {"name": "Convites", "url": "/users/convites/", "icon": "bi-envelope-paper", "perm": "staff"},
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
            or (item["perm"] == "staff" and is_staff)
            or (item["perm"] == "owner" and is_owner)
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
    if user.groups.filter(name__in=["admin", "operator"]).exists():
        return True
    # N-106: an owner or operator of an organization runs its own nodes, accounts and presets
    return user.memberships.filter(role__in=["owner", "operator"]).exists()


def _is_staff(user):
    """The platform's own team: the only ones who see the platform's pages (N-106)."""
    return bool(user and getattr(user, "is_authenticated", False) and (user.is_superuser or user.is_staff))


def _is_owner(user):
    if _is_staff(user):
        return True
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return user.memberships.filter(role="owner").exists()


def hub_version(request):
    """Provide hub version in templates."""
    return {"HUB_VERSION": settings.VERSION}


def htmx_context(request):
    """Provide HTMX-aware base template selection."""
    is_htmx = request.headers.get("HX-Request") == "true"
    is_boosted = request.headers.get("HX-Boosted") == "true"
    base = "base_partial.html" if is_htmx and is_boosted else "base.html"
    return {"base_template": base, "is_htmx": is_htmx}
