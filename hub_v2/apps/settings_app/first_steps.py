"""First steps of an installation (N-116): each piece the system needs, tested for real.

Every check answers the same shape: a short title, a state (ok, warn, missing or optional), one
line on what was found and, when something is missing, where to fix it. Nothing is written.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import requests
from django.conf import settings

GATEWAY_HEALTH_URL = "http://gateway:8001/__ikabot/health"
TIMEOUT = 5


def _item(key: str, title: str, icon: str, state: str, detail: str, *, link: str = "", link_label: str = "") -> dict:
    return {"key": key, "title": title, "icon": icon, "state": state, "detail": detail, "link": link, "link_label": link_label}


def check_ikabotapi() -> dict:
    from .services import test_ikabotapi
    from .views import _get_setting

    url = _get_setting("ikabotapi_url", getattr(settings, "IKABOTAPI_URL", ""))
    result = test_ikabotapi(url)
    if result["success"]:
        return _item("ikabotapi", "IkabotAPI", "bi-key", "ok", "Respondendo: o login no jogo e os captchas têm quem os resolva.")
    return _item("ikabotapi", "IkabotAPI", "bi-key", "missing", f"Não respondeu ({result['error']}). Sem ele o hub não entra no jogo.",
                 link="/settings/", link_label="Configurações")


def check_agents() -> dict:
    from apps.accounts.models import Node

    nodes = list(Node.objects.all())
    online = [node for node in nodes if node.is_online]
    if online:
        names = ", ".join(node.name for node in online[:3]) + ("…" if len(online) > 3 else "")
        return _item("agents", "Agente", "bi-hdd-network", "ok", f"{len(online)} de {len(nodes)} conectado{'s' if len(online) != 1 else ''}: {names}.")
    if nodes:
        return _item("agents", "Agente", "bi-hdd-network", "missing", "Nenhum agente falou com o hub nos últimos 3 minutos.",
                     link="/accounts/nodes/", link_label="Nós & Agents")
    return _item("agents", "Agente", "bi-hdd-network", "missing", "Nenhum agente se apresentou ainda: confira o container agent e o AGENT_TOKEN.",
                 link="/accounts/nodes/", link_label="Nós & Agents")


def check_accounts() -> dict:
    from apps.accounts.models import GameAccount

    total = GameAccount.objects.filter(active=True).count()
    if total:
        return _item("accounts", "Contas do jogo", "bi-person-badge", "ok", f"{total} conta{'s' if total != 1 else ''} ativa{'s' if total != 1 else ''}.")
    return _item("accounts", "Contas do jogo", "bi-person-badge", "missing", "Nenhuma conta cadastrada.", link="/accounts/", link_label="Contas do Jogo")


def check_address(request) -> dict:
    public = (getattr(settings, "HUB_PUBLIC_URL", "") or "").strip()
    opened = f"{request.scheme}://{request.get_host()}"
    if not public:
        return _item("address", "Endereço", "bi-globe", "warn",
                     f"HUB_PUBLIC_URL vazio no .env. Aberto agora por {opened}; preencha com o endereço da barra do navegador.")
    parts = urlsplit(public)
    secure = parts.scheme == "https"
    same = parts.netloc.lower() == request.get_host().lower()
    detail = f"{public}{' com HTTPS' if secure else ' sem HTTPS'}."
    if not same:
        return _item("address", "Endereço", "bi-globe", "warn", f"{detail} Esta página foi aberta por {request.get_host()}: o login pode falhar por outro endereço.")
    return _item("address", "Endereço", "bi-globe", "ok", detail)


def check_telegram() -> dict:
    from apps.telegram.models import TelegramBotConfig

    config = TelegramBotConfig.objects.filter(pk=1).first()
    if not config or not config.bot_token:
        return _item("telegram", "Telegram", "bi-telegram", "optional", "Sem bot configurado: avisos e botões ficam só no painel.", link="/telegram/", link_label="Telegram")
    if not config.enabled:
        return _item("telegram", "Telegram", "bi-telegram", "warn", "Bot configurado, mas desligado.", link="/telegram/", link_label="Telegram")
    public = getattr(settings, "HUB_PUBLIC_URL", "") or ""
    channel = ("Recebe pelo webhook (HTTPS)." if public.startswith("https://") and config.webhook_secret
               else "Sem webhook: o serviço telegram (perfil telegram do compose) busca as mensagens e os botões.")
    if config.link_status != "linked":
        return _item("telegram", "Telegram", "bi-telegram", "warn", f"Bot ligado, chat ainda não vinculado. {channel}", link="/telegram/", link_label="Telegram")
    return _item("telegram", "Telegram", "bi-telegram", "ok", f"Bot ligado e chat vinculado. {channel}")


def check_manual_control() -> dict:
    domain = getattr(settings, "MANUAL_GATEWAY_DOMAIN", "")
    if not domain:
        return _item("manual", "Controle manual", "bi-joystick", "optional", "Desligado: precisa de um domínio com host curinga (MANUAL_GATEWAY_DOMAIN).",
                     link="https://github.com/rdgweb/ikabot-web/blob/main/docs/proxy.md", link_label="Guia")
    try:
        ok = requests.get(GATEWAY_HEALTH_URL, timeout=TIMEOUT).status_code == 200
    except requests.RequestException:
        ok = False
    if ok:
        return _item("manual", "Controle manual", "bi-joystick", "ok", f"Gateway respondendo; sessões em *.{domain}.")
    return _item("manual", "Controle manual", "bi-joystick", "missing", f"Domínio *.{domain} configurado, mas o gateway não respondeu.")


def check_supervisor() -> dict:
    from apps.accounts.models import DockerHost

    hosts = list(DockerHost.objects.all())
    online = [host for host in hosts if host.is_online]
    if online:
        return _item("supervisor", "Supervisor", "bi-arrow-repeat", "ok", f"{len(online)} máquina{'s' if len(online) != 1 else ''} pronta{'s' if len(online) != 1 else ''} para atualizar agentes pelo painel.")
    if hosts:
        return _item("supervisor", "Supervisor", "bi-arrow-repeat", "warn", "Máquina cadastrada, mas o supervisor não falou com o hub nos últimos 2 minutos.",
                     link="/accounts/nodes/", link_label="Nós & Agents")
    return _item("supervisor", "Supervisor", "bi-arrow-repeat", "optional", "Sem supervisor: agentes se atualizam com docker compose pull.",
                 link="/accounts/nodes/", link_label="Nós & Agents")


STATUS_ACTIONS = (4, 100)          # read the account: cities, resources, buildings
DISCOVER_ACTION = 101


def _step(number: int, key: str, title: str, detail: str, done: bool, link: str, button: str, icon: str) -> dict:
    return {"number": number, "key": key, "title": title, "detail": detail, "done": done, "link": link, "button": button, "icon": icon}


def first_use() -> list[dict]:
    """The way from an empty hub to the first action in the game, each step marked by what exists."""
    from apps.accounts.models import Account, GameAccount
    from apps.game.models import AccountSnapshot
    from apps.jobs.models import Job

    account = Account.objects.order_by("created_at").first()
    jobs = Job.objects.exclude(action_code=DISCOVER_ACTION)
    return [
        _step(1, "account", "Cadastrar a conta", "E-mail e senha do GameForge, e o nó que vai jogar por ela.",
              account is not None, "/accounts/?guia=nova-conta", "Nova Conta", "bi-plus-lg"),
        _step(2, "worlds", "Trazer os mundos", "Cada servidor em que a conta joga vira uma subconta.",
              GameAccount.objects.filter(active=True).exists(),
              f"/accounts/{account.pk}/?guia=discover" if account else "/accounts/", "Discover Characters", "bi-search"),
        _step(3, "status", "Ler a conta", "Novo Job → subconta → Verificar Status: cidades, recursos e edifícios vão para o Painel do Jogo.",
              AccountSnapshot.objects.exists(), "/game/?guia=novo-job", "Novo Job", "bi-plus-lg"),
        _step(4, "queue", "Acompanhar", "Na fila, executando, concluído ou erro; o job aberto mostra o log.",
              jobs.filter(status="finished").exists(), "/jobs/", "Fila de Jobs", "bi-list-task"),
        _step(5, "action", "Primeira ação", "Construir, transportar, pesquisar, vender: escolha a ação e a subconta.",
              jobs.exclude(action_code__in=STATUS_ACTIONS).exists(), "/game/actions/", "Ações do Jogo", "bi-controller"),
    ]


def run_checks(request) -> list[dict]:
    checks = []
    for check in (check_ikabotapi, check_agents, check_accounts, lambda: check_address(request), check_telegram, check_manual_control, check_supervisor):
        try:
            checks.append(check())
        except Exception as exc:  # noqa: BLE001 - one broken check must not take the page down
            checks.append(_item("error", "Verificação", "bi-exclamation-triangle", "warn", f"Não foi possível conferir: {exc}"))
    return checks
