"""Explicit capabilities. Unknown permissions never grant access."""

PLATFORM_AREAS = {
    "api": "Documentação da API", "access": "Permissões e acessos",
    "organizations": "Organizações", "notes": "Notas", "releases": "Atualizações",
    "settings": "Configurações", "maintenance": "Manutenção", "usage": "Uso",
    "infrastructure": "Hosts Docker", "integrations": "Integrações globais",
    "telemetry": "Telemetria", "invites": "Convites",
}
ORG_AREAS = {
    "dashboard": "Dashboard", "accounts": "Contas", "nodes": "Agents",
    "presets": "Presets", "jobs": "Jobs", "game": "Jogo",
    "market": "Mercado", "piracy": "Pirataria", "intelligence": "Inteligência",
    "combat": "Combates", "diplomacy": "Diplomacia", "generals": "Banco de generais",
    "organization": "Organização", "members": "Membros", "billing": "Plano",
    "integrations": "Integrações", "audit": "Atividades", "access": "Papéis e acessos",
}
CATALOG = {
    f"{scope}.{area}.{operation}": f"{label} · {verb}"
    for scope, areas in (("platform", PLATFORM_AREAS), ("organization", ORG_AREAS))
    for area, label in areas.items()
    for operation, verb in (("view", "consultar"), ("manage", "gerenciar"))
}
# Ownership is reserved to the immutable owner role, never a custom role checkbox.
OWNER = {p for p in CATALOG if p.startswith("organization.")}
OPERATOR = {p for p in OWNER if p.split(".")[1] not in ("members", "billing", "access")}
OPERATOR.discard("organization.organization.manage")
OPERATOR |= {"organization.members.view", "organization.billing.view"}
OPERATOR.discard("organization.audit.view")
OPERATOR.discard("organization.audit.manage")
VIEWER = {p for p in OPERATOR if p.endswith(".view")}
LEGACY_PLATFORM = {p for p in CATALOG if p.startswith("platform.") and ".access." not in p}


def required_permission(path, method="GET"):
    """Route families share a permission; state-changing requests always need manage."""
    write = method not in ("GET", "HEAD", "OPTIONS")
    if any(part in path for part in ("/create/", "/new/", "/edit/", "/delete/", "/start/", "/run/", "/control/", "/cancel/", "/retry/", "/run-now/", "/restart/", "/unstuck/")):
        write = True
    operation = "manage" if write else "view"
    routes = (
        ("/users/permissoes/", "platform.access"),
        ("/users/convites/", "platform.invites"),
        ("/api/docs/", "platform.api"), ("/api/schema/", "platform.api"),
        ("/settings/manutencao/", "platform.maintenance"),
        ("/settings/uso/", "platform.usage"),
        ("/settings/", "platform.settings"),
        ("/notes/atualizacoes/", "platform.releases"), ("/notes/", "platform.notes"),
        ("/sistema/", "platform.organizations"),
        ("/accounts/docker-hosts/", "platform.infrastructure"),
        ("/telemetry/", "platform.telemetry"),
        ("/telegram/", "platform.integrations"), ("/proxy/", "platform.integrations"),
        ("/captcha/", "platform.integrations"),
        ("/organizacao/acessos/", "organization.access"),
        ("/organizacao/membros/", "organization.members"),
        ("/organizacao/plano/", "organization.billing"),
        ("/organizacao/subcontas/", "organization.accounts"),
        ("/organizacao/proxies/", "organization.integrations"),
        ("/organizacao/telegram/", "organization.integrations"),
        ("/organizacao/", "organization.organization"),
        ("/accounts/nodes/", "organization.nodes"), ("/accounts/", "organization.accounts"),
        ("/profiles/", "organization.presets"), ("/jobs/", "organization.jobs"),
        ("/game/", "organization.game"), ("/market/", "organization.market"),
        ("/pirataria/", "organization.piracy"), ("/intel/", "organization.intelligence"),
        ("/espionage/", "organization.intelligence"), ("/combat/", "organization.combat"),
        ("/diplomacy/", "organization.diplomacy"), ("/generais/", "organization.generals"),
        ("/atividades/", "organization.audit"),
    )
    for prefix, area in routes:
        if path.startswith(prefix):
            return f"{area}.{operation}"
    if path == "/":
        return f"organization.dashboard.{operation}"
    return None
