"""Template context processors for global template data."""

from django.conf import settings


def nav_context(request):
    """Provide sidebar navigation context filtered by user permissions."""
    user = getattr(request, "user", None)
    from apps.users.models import UserProfile
    from django.db.models import BooleanField, Case, Value, When
    profile = UserProfile.objects.filter(user=user).defer("avatar").annotate(
        has_avatar=Case(When(avatar__isnull=False, then=Value(True)), default=Value(False), output_field=BooleanField())).first() if user and user.is_authenticated else None
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
                {"name": "Templos e Fé", "url": "/game/temples/", "icon": "bi-stars"},
                {"name": "Mercado Interno", "url": "/market/", "icon": "bi-shop"},
                {"name": "Mercado Negro", "url": "/market/black-market/", "icon": "bi-shield-exclamation"},
                {"name": "Banco de Generais", "url": "/generais/", "icon": "bi-shield-fill-check"},
                {"name": "Diplomacia", "url": "/diplomacy/", "icon": "bi-envelope"},
                {"name": "Espionagem", "url": "/espionage/", "icon": "bi-binoculars"},
                {"name": "Pirataria", "url": "/pirataria/", "icon": "bi-flag"},
                {"name": "Combates", "url": "/combat/", "icon": "bi-shield-shaded"},
                {"name": "Fila de Jobs", "url": "/jobs/", "icon": "bi-list-task"},
            ],
        },
        {
            # N-106: what each organization sets for itself
            "label": "Minha organização",
            "items": [
                {"name": "Visão geral", "url": "/organizacao/", "icon": "bi-building-gear"},
                {"name": "Suporte", "url": "/suporte/", "icon": "bi-life-preserver"},
                {"name": "Subcontas", "url": "/organizacao/subcontas/", "icon": "bi-toggles"},
                {"name": "Proxies e agente", "url": "/organizacao/proxies/", "icon": "bi-shield-lock"},
                {"name": "Membros", "url": "/organizacao/membros/", "icon": "bi-people"},
                {"name": "Papéis e acessos", "url": "/organizacao/acessos/", "icon": "bi-person-lock"},
                {"name": "Trocar organização", "url": "/organizacao/selecionar/", "icon": "bi-buildings"},
                {"name": "Telegram", "url": "/organizacao/telegram/", "icon": "bi-telegram"},
                {"name": "Plano", "url": "/organizacao/plano/", "icon": "bi-credit-card", "perm": "billing"},
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
                {"name": "Atendimento", "url": "/suporte/atendimento/", "icon": "bi-headset", "perm": "staff"},
                {"name": "Atualizações", "url": "/notes/atualizacoes/", "icon": "bi-journal-code", "perm": "staff"},
                {"name": "Atividades", "url": "/atividades/", "icon": "bi-clock-history", "perm": "access"},
                {"name": "Configurações", "url": "/settings/", "icon": "bi-gear", "perm": "staff"},
                {"name": "Manutenção", "url": "/settings/manutencao/", "icon": "bi-tools", "perm": "staff"},
                {"name": "Uso por organização", "url": "/settings/uso/", "icon": "bi-pie-chart", "perm": "staff"},
                {"name": "Usuários", "url": "/users/", "icon": "bi-people", "perm": "principal"},
                {"name": "Permissões e acessos", "url": "/users/permissoes/", "icon": "bi-person-lock", "perm": "access"},
                {"name": "Documentação da API", "url": "/api/docs/", "icon": "bi-braces", "perm": "staff", "full_page": True},
                {"name": "Organizações", "url": "/sistema/organizacoes/", "icon": "bi-buildings", "perm": "staff"},
                {"name": "Convites", "url": "/users/convites/", "icon": "bi-envelope-paper", "perm": "staff"},
            ],
        },
    ]

    filtered = []
    from apps.access_control.models import AccessMode
    organization = getattr(request, "organization", None)
    has_custom_access = bool(organization and user.is_authenticated and AccessMode.objects.filter(user=user, scope_key=str(organization.pk)).exists())
    for section in all_sections:
        section_perm = section.get("perm")
        if section_perm == "admin" and not is_admin:
            continue
        if section_perm == "operator" and not (is_operator or is_staff or has_custom_access):
            continue

        items = [
            item for item in section["items"]
            if not item.get("perm")
            or (item["perm"] == "admin" and is_admin)
            or (item["perm"] == "operator" and is_operator)
            or (item["perm"] == "staff" and is_staff)
            or (item["perm"] == "principal" and user.is_superuser and user.is_active)
            or (item["perm"] == "owner" and is_owner)
            or (item["perm"] == "billing" and _billing_open(is_staff))
            or item["perm"] == "access"
        ]
        from apps.access_control.catalog import required_permission
        from apps.access_control.policy import allowed
        items = [item for item in items if item["url"] == "/organizacao/selecionar/" or
                 not required_permission(item["url"]) or
                 allowed(user, required_permission(item["url"]), getattr(request, "organization", None))]
        if items:
            filtered.append({"label": section["label"], "items": items})

    return {
        "NAV_SECTIONS": filtered,
        "user_is_admin": is_admin,
        "user_is_operator": is_operator,
        "user_is_staff": is_staff,
        "personal_profile": profile,
        "personal_display_name": (profile.display_name if profile else "") or getattr(user, "first_name", "") or getattr(user, "username", ""),
    }


def _billing_open(is_staff: bool) -> bool:
    """N-208: the plan in the menu only while subscriptions are open (the system's administrator always sees it)."""
    if is_staff:
        return True
    from apps.billing.services import billing_open

    return billing_open()


def _is_admin(user):
    from core.access import platform_admin
    return platform_admin(user)


def _is_operator(user):
    from core.access import operator
    return operator(user)


def _is_staff(user):
    from core.access import platform_admin
    return platform_admin(user)


def _is_owner(user):
    from core.access import owner
    return owner(user)


def hub_version(request):
    """Provide hub version in templates."""
    return {"HUB_VERSION": settings.VERSION}


def htmx_context(request):
    """Provide HTMX-aware base template selection."""
    is_htmx = request.headers.get("HX-Request") == "true"
    is_boosted = request.headers.get("HX-Boosted") == "true"
    base = "base_partial.html" if is_htmx and is_boosted else "base.html"
    return {"base_template": base, "is_htmx": is_htmx}
