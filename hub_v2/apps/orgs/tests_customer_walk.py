"""N-126: walk the whole panel as a customer — no page may fail and none may show the platform's data."""
import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import Account, GameAccount, Node
from apps.orgs.models import Membership, Organization

SECRETS = ("JogadorDaPlataforma", "Conta da plataforma", "no-plataforma", "plataforma@example.com", "rodrigo_tg", "999111")
SKIP = ("/logout/", "/static/", "/api/", "mailto:", "javascript:", "#", "https://", "http://", "/stats/", "/conta/confirmar",
        "/senha/nova", "/convite/")


@override_settings(ASAAS_API_KEY="k", ASAAS_SANDBOX=True)
class CustomerWalkTests(TestCase):
    def test_every_page_a_customer_reaches_works_and_shows_only_its_data(self):
        cache.clear()
        staff = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        pnode = Node.objects.create(name="no-plataforma")
        pacc = Account.objects.create(node=pnode, label="Conta da plataforma", email="plataforma@example.com", password_enc="x")
        GameAccount.objects.create(account=pacc, lobby_account_id=1, server_id="s1-br", name="JogadorDaPlataforma",
                                   server_language="br", server_number=1)
        user = get_user_model()(username="cliente")
        user._own_organization = True
        user.set_password("x")
        user.save()
        org = Organization.objects.create(name="Blood", slug="blood")
        Membership.objects.create(organization=org, user=user, role="owner")
        cnode = Node.objects.create(name="no-cliente", organization=org, transport="https")
        cacc = Account.objects.create(node=cnode, label="Conta do cliente", email="c@example.com", password_enc="x", organization=org)
        GameAccount.objects.create(account=cacc, lobby_account_id=2, server_id="s2-br", name="JogadorDoCliente",
                                   server_language="br", server_number=2)
        from apps.telegram.models import TelegramBotConfig

        TelegramBotConfig.objects.update_or_create(pk=1, defaults={"enabled": True, "bot_token": "1:x", "chat_id": "999111",
                                                                  "telegram_username": "rodrigo_tg"})
        # the platform looks first, so every cache is warm with its data
        self.client.force_login(staff)
        for url in ("/", "/game/", "/jobs/", "/accounts/", "/accounts/nodes/", "/market/", "/intel/players/"):
            self.client.get(url)
        self.client.force_login(user)
        seen, queue, report = set(), ["/"], []
        from core.context_processors import nav_context
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        request.user = user
        for section in nav_context(request)["NAV_SECTIONS"]:
            for item in section["items"]:
                queue.append(item["url"])
        while queue and len(seen) < 160:
            url = queue.pop(0)
            if url in seen or any(url.startswith(s) for s in SKIP):
                continue
            seen.add(url)
            try:
                response = self.client.get(url, HTTP_HX_REQUEST="false")
            except Exception as exc:  # noqa: BLE001
                report.append(f"EXC  {url}  {type(exc).__name__}: {str(exc)[:120]}")
                continue
            code = response.status_code
            if code in (301, 302):
                continue
            body = response.content.decode(errors="ignore") if response.get("Content-Type", "").startswith("text/html") else ""
            leaked = [s for s in SECRETS if s in body]
            if code >= 400 or leaked:
                report.append(f"{code}  {url}  {'VAZA: ' + ', '.join(leaked) if leaked else ''}")
            for href in re.findall(r'(?:href|hx-get)="(/[^"#?]*)', body):
                if href not in seen and not any(href.startswith(s) for s in SKIP):
                    queue.append(href)
        self.assertGreater(len(seen), 30)
        self.assertEqual(report, [])
