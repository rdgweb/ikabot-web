from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Account, Node
from apps.proxy.models import AccountProxyReservation, ProxyProfile


class AccountLobbyProxiesViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.credentials(HTTP_X_AGENT_TOKEN=settings.AGENT_TOKEN)

        self.node = Node.objects.create(name="node-a")
        self.account = Account.objects.create(
            node=self.node,
            label="Lobby A",
            email="lobby-a@example.com",
            password_enc="enc",
        )
        self.other_account = Account.objects.create(
            node=self.node,
            label="Lobby B",
            email="lobby-b@example.com",
            password_enc="enc",
        )

    def _proxy(self, host: str, *, ok: bool = True) -> ProxyProfile:
        return ProxyProfile.objects.create(
            address=host,
            port=8080,
            active=True,
            last_test_status=ok,
        )

    def test_endpoint_reserves_up_to_three_unique_proxies_for_one_lobby(self):
        p1 = self._proxy("10.0.0.1")
        p2 = self._proxy("10.0.0.2")
        p3 = self._proxy("10.0.0.3")
        p4 = self._proxy("10.0.0.4")

        AccountProxyReservation.objects.create(account=self.other_account, proxy_profile=p4)

        response = self.client.post(
            f"/api/agent/accounts/{self.account.pk}/lobby-proxies/",
            {"limit": 3},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["proxies"]), 3)

        reserved_ids = {
            str(res.proxy_profile_id)
            for res in AccountProxyReservation.objects.filter(account=self.account)
        }
        self.assertEqual(
            reserved_ids,
            {str(p1.pk), str(p2.pk), str(p3.pk)},
        )
        self.assertNotIn(str(p4.pk), reserved_ids)

        second = self.client.post(
            f"/api/agent/accounts/{self.account.pk}/lobby-proxies/",
            {"limit": 3},
            format="json",
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            {item["id"] for item in second.json()["proxies"]},
            {str(p1.pk), str(p2.pk), str(p3.pk)},
        )

    def test_endpoint_does_not_expand_beyond_three_historical_reservations(self):
        p1 = self._proxy("10.0.1.1", ok=True)
        p2 = self._proxy("10.0.1.2", ok=False)
        p3 = self._proxy("10.0.1.3", ok=True)
        p4 = self._proxy("10.0.1.4", ok=True)

        AccountProxyReservation.objects.create(account=self.account, proxy_profile=p1)
        AccountProxyReservation.objects.create(account=self.account, proxy_profile=p2)
        AccountProxyReservation.objects.create(account=self.account, proxy_profile=p3)

        response = self.client.post(
            f"/api/agent/accounts/{self.account.pk}/lobby-proxies/",
            {"limit": 3},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            AccountProxyReservation.objects.filter(account=self.account).count(),
            3,
        )
        self.assertNotIn(str(p4.pk), {
            str(res.proxy_profile_id)
            for res in AccountProxyReservation.objects.filter(account=self.account)
        })
