"""N-203: a tester with the platform's proxies and agent on probes every route of the hub — every GET and an empty
POST, with its own ids and the platform's. No answer may carry a secret, and nothing of the platform (or its own
platform-managed pieces and plan) may change."""
import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils import timezone

from apps.accounts.models import Account, DockerHost, GameAccount, Node
from apps.billing.models import BillingAccount
from apps.billing.services import account_for, set_agent_addon, set_proxy_addon
from apps.notes.services import RegistryRelease
from apps.proxy.models import ProxyProfile
from apps.settings_app.models import AppSetting
from core.encryption import encrypt

from .models import Membership, Organization
from .services import default_organization

RELEASE = RegistryRelease(component="agent", repository="blackoneal/ikabot-web-agent", version="0.1.178",
                          digest="sha256:" + "d" * 64)
SECRETS = ("SENHA-PROXY-PLAT", "usr-plat", "SENHA-JOGO-PLAT", "SENHA-JOGO-CLI", "GFTOKEN-PLAT", "GFTOKEN-CLI",
           "TOKEN-DO-BOT", "plataforma@example.com", "JogadorDaPlataforma", "Conta da plataforma", "no-plataforma")
SKIP = ("logout", "static", "media", "__debug__", "conta/confirmar", "senha/nova")


def _routes(patterns, prefix=""):
    for p in patterns:
        route = str(p.pattern)
        if isinstance(p, URLResolver):
            yield from _routes(p.url_patterns, prefix + route)
        elif isinstance(p, URLPattern):
            yield prefix + route


@override_settings(ASAAS_API_KEY="k", ASAAS_SANDBOX=True, PLATFORM_AGENT_LIMIT=5)
class CustomerProbeTests(TestCase):
    def setUp(self):
        cache.clear()
        platform = default_organization()
        self.host = DockerHost._base_manager.create(name="docker-plataforma", organization=platform, last_seen_at=timezone.now())
        ctx = override_settings(PLATFORM_AGENT_HOST_ID=str(self.host.pk))
        ctx.enable()
        self.addCleanup(ctx.disable)
        release = patch("apps.notes.services.get_registry_release", return_value=RELEASE)
        release.start()
        self.addCleanup(release.stop)
        self.staff = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True, is_superuser=True)
        for i in range(30):
            ProxyProfile._base_manager.create(organization=platform, provider="webshare", webshare_id=f"w{i}", address=f"10.9.0.{i}",
                                              port=7000 + i, username="usr-plat", password_enc=encrypt("SENHA-PROXY-PLAT"),
                                              active=True, last_test_status=True, last_test_latency_ms=50)
        self.pnode = Node.objects.create(name="no-plataforma", organization=platform,
                                         proxy="http://usr-plat:SENHA-PROXY-PLAT@10.9.0.99:7999")
        self.pacc = Account.objects.create(node=self.pnode, label="Conta da plataforma", email="plataforma@example.com",
                                           password_enc=encrypt("SENHA-JOGO-PLAT"), gf_token_enc=encrypt("GFTOKEN-PLAT"),
                                           organization=platform)
        self.pworld = GameAccount.objects.create(account=self.pacc, lobby_account_id=1, server_id="s1-br", name="JogadorDaPlataforma",
                                                 server_language="br", server_number=1)
        from apps.telegram.models import TelegramBotConfig

        TelegramBotConfig.objects.update_or_create(pk=1, defaults={"enabled": True, "bot_token": "1:TOKEN-DO-BOT", "chat_id": "1"})

        self.user = get_user_model()(username="testador")
        self.user._own_organization = True
        self.user.set_password("x")
        self.user.save()
        self.org = Organization.objects.create(name="Teste", slug="teste")
        Membership.objects.create(organization=self.org, user=self.user, role="owner")
        own = Node.objects.create(name="pc-testador", organization=self.org, transport="https")
        self.acc = Account._base_manager.create(node=own, label="Conta T", email="t@example.com", organization=self.org,
                                                password_enc=encrypt("SENHA-JOGO-CLI"), gf_token_enc=encrypt("GFTOKEN-CLI"))
        self.world = GameAccount.objects.create(account=self.acc, lobby_account_id=2, server_id="s2-br", name="JogadorT",
                                                server_language="br", server_number=2)
        billing = account_for(self.org)
        assert set_proxy_addon(billing, self.acc, True)["ok"]
        assert set_agent_addon(billing, self.acc, True)["ok"]
        self.acc.refresh_from_db()

    def _snapshot(self):
        cnode = Node._base_manager.get(pk=self.acc.platform_agent_node_id)
        billing = BillingAccount.objects.get(organization=self.org)
        return {
            "platform node": list(Node._base_manager.filter(pk=self.pnode.pk).values("name", "proxy", "active", "organization_id")),
            "platform account": list(Account._base_manager.filter(pk=self.pacc.pk).values("label", "password_enc", "node_id", "active")),
            "platform world": list(GameAccount._base_manager.filter(pk=self.pworld.pk).values("name", "active")),
            "platform proxies": sorted(ProxyProfile._base_manager.filter(provider="webshare")
                                       .values_list("address", "port", "username", "password_enc", "active")),
            "docker host": list(DockerHost._base_manager.values("name", "active", "organization_id")),
            # its own tutorial mark is its to change
            "settings": sorted(AppSetting.objects.exclude(key__in=[f"tour_done:{self.user.pk}", f"bell_seen:{self.user.pk}"])
                               .values_list("key", "value")),
            "organizations": sorted(Organization._base_manager.values_list("name", "slug", "active")),
            "staff": sorted(get_user_model().objects.values_list("username", "is_staff", "is_superuser")),
            "plan": (billing.status, billing.limit, billing.trial_ends_at, billing.addons_free_until),
            "its platform node": (cnode.platform_managed, cnode.proxy, cnode.organization_id, cnode.docker_host_id),
        }

    def test_nothing_secret_is_shown_and_nothing_of_the_platform_changes(self):
        before = self._snapshot()
        # forged: its account onto the platform's node, add-ons on the platform's account, a lent proxy removed
        self.client.force_login(self.user)
        self.client.post(f"/accounts/{self.acc.pk}/edit/", {"node": str(self.pnode.pk), "label": "Conta T", "email": "t@example.com",
                                                            "active": "on"})
        self.assertNotEqual(Account._base_manager.get(pk=self.acc.pk).node_id, self.pnode.pk)
        for action in ("addon", "agent"):
            self.client.post("/organizacao/proxies/", {"action": action, "account": str(self.pacc.pk), "on": "1"})
        self.client.post("/organizacao/proxies/", {"action": "remove",
                                                   "proxy": str(ProxyProfile._base_manager.filter(lent_to=self.acc).first().pk)})
        ids = [str(x) for x in (self.pnode.pk, self.pacc.pk, self.acc.platform_agent_node_id, self.acc.pk, self.host.pk,
                                default_organization().pk, self.org.pk,
                                ProxyProfile._base_manager.filter(lent_to=self.acc).values_list("pk", flat=True).first(),
                                ProxyProfile._base_manager.filter(lent_to__isnull=True).values_list("pk", flat=True).first())]
        ints = [str(self.pworld.pk), str(self.world.pk), "1"]
        urls = set()
        for route in _routes(get_resolver().url_patterns):
            if route.startswith("^") or any(s in route for s in SKIP):
                continue
            params = re.findall(r"<(?:(\w+):)?(\w+)>", route)
            if not params:
                urls.add("/" + route)
                continue
            pool = ints if params[0][0] == "int" else ids
            for value in pool:
                url = route
                for conv, _name in params:
                    url = re.sub(r"<(?:\w+:)?\w+>", value if conv != "int" else ints[0], url, count=1)
                urls.add("/" + url)
        leaks, opened = [], []
        for method in ("get", "post"):
            for n, url in enumerate(sorted(urls)):
                if n % 40 == 0:
                    self.client.force_login(self.user)
                try:
                    with patch("apps.billing.asaas.create_charge", return_value=("p", "https://x")), \
                         patch("apps.billing.asaas.update_subscription_value"), \
                         patch("apps.billing.asaas.create_customer", return_value="c", create=True):
                        response = getattr(self.client, method)(url)
                except Exception as exc:  # noqa: BLE001 — a crash is not a leak; reported apart
                    opened.append(f"EXC {method} {url} {type(exc).__name__}")
                    continue
                body = b"".join(response.streaming_content) if response.streaming else response.content
                text = body.decode(errors="ignore") + " " + response.get("Location", "")
                found = [s for s in SECRETS if s in text]
                if found:
                    leaks.append(f"{method.upper()} {url} {response.status_code}: {', '.join(found)}")
        after = self._snapshot()
        changed = [k for k in before if before[k] != after[k]]
        if "settings" in changed:
            print("  SETTINGS", sorted(set(after["settings"]) ^ set(before["settings"]))[:20])
        print(f"\nPROBE: {len(urls)} rotas x GET/POST; vazamentos={len(leaks)} alterado={changed}")
        for line in leaks[:40]:
            print("  VAZA", line)
        for line in opened[:40]:
            print("  ", line)
        self.assertEqual(leaks, [])
        self.assertEqual(changed, [], {k: (before[k], after[k]) for k in changed})
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff or self.user.is_superuser)
