import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.test.client import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot, AccountSnapshotHistory
from apps.game.views.dashboard import DashboardView


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
class PatchSnapshotResourcesTests(TestCase):
    def setUp(self):
        node = Node.objects.create(name="agent-1")
        self.account = Account.objects.create(
            node=node,
            label="Lobby",
            email="test@example.com",
            password_enc="enc",
        )
        self.game_account = GameAccount.objects.create(
            account=self.account,
            lobby_account_id=123,
            server_id="s1-br",
            server_language="br",
            server_number=1,
            name="Player",
        )
        self.snapshot = AccountSnapshot.objects.create(
            account=self.account,
            game_account=self.game_account,
            base_snapshot={},
            cities=[
                {
                    "id": "42",
                    "name": "Wine City",
                    "wood": 100,
                    "wine": 200,
                    "current_resources": {"resource": 100, "1": 200},
                }
            ],
            military={},
        )

    def test_patch_resources_updates_city_fields_and_current_resources(self):
        response = self.client.patch(
            "/api/agent/snapshots/patch-resources/",
            data=json.dumps(
                {
                    "game_account_id": str(self.game_account.pk),
                    "city_id": "42",
                    "resources": {"wine": 1234, "wood": 99},
                }
            ),
            content_type="application/json",
            HTTP_X_AGENT_TOKEN="test-agent-token",
        )

        self.assertEqual(response.status_code, 200)
        self.snapshot.refresh_from_db()
        city = self.snapshot.cities[0]
        self.assertEqual(city["wine"], 1234)
        self.assertEqual(city["wood"], 99)
        self.assertEqual(city["current_resources"]["1"], 1234)
        self.assertEqual(city["current_resources"]["resource"], 99)

    def test_patch_incoming_delta_accumulates_without_overwriting_stock(self):
        response = self.client.patch(
            "/api/agent/snapshots/patch-resources/",
            data=json.dumps(
                {
                    "game_account_id": str(self.game_account.pk),
                    "city_id": "42",
                    "incoming_delta": {"wine": 500},
                }
            ),
            content_type="application/json",
            HTTP_X_AGENT_TOKEN="test-agent-token",
        )

        self.assertEqual(response.status_code, 200)
        self.snapshot.refresh_from_db()
        city = self.snapshot.cities[0]
        self.assertEqual(city["wine"], 200)
        self.assertEqual(city["incoming_resources"]["wine"], 500)

        response = self.client.patch(
            "/api/agent/snapshots/patch-resources/",
            data=json.dumps(
                {
                    "game_account_id": str(self.game_account.pk),
                    "city_id": "42",
                    "incoming_delta": {"wine": -200},
                }
            ),
            content_type="application/json",
            HTTP_X_AGENT_TOKEN="test-agent-token",
        )

        self.assertEqual(response.status_code, 200)
        self.snapshot.refresh_from_db()
        self.assertEqual(self.snapshot.cities[0]["incoming_resources"]["wine"], 300)

    def test_patch_base_updates_gold_without_full_snapshot_refresh(self):
        self.snapshot.base_snapshot = {"gold": 100, "income": 50}
        self.snapshot.save(update_fields=["base_snapshot", "updated_at"])

        response = self.client.patch(
            "/api/agent/snapshots/patch-base/",
            data=json.dumps(
                {
                    "game_account_id": str(self.game_account.pk),
                    "patch": {"gold": 4321},
                }
            ),
            content_type="application/json",
            HTTP_X_AGENT_TOKEN="test-agent-token",
        )

        self.assertEqual(response.status_code, 200)
        self.snapshot.refresh_from_db()
        self.assertEqual(self.snapshot.base_snapshot["gold"], 4321)
        self.assertEqual(self.snapshot.base_snapshot["income"], 50)


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
class DashboardCacheInvalidationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="tester",
            email="tester@example.com",
            password="secret123",
        )
        node = Node.objects.create(name="agent-cache")
        self.account = Account.objects.create(
            node=node,
            label="Lobby Cache",
            email="cache@example.com",
            password_enc="enc",
        )
        self.game_account = GameAccount.objects.create(
            account=self.account,
            lobby_account_id=321,
            server_id="s2-br",
            server_language="br",
            server_number=2,
            name="CachePlayer",
        )
        AccountSnapshot.objects.create(
            account=self.account,
            game_account=self.game_account,
            base_snapshot={"gold": 100, "income": 50, "upkeep": -10, "scientists_upkeep": -5},
            cities=[],
            military={},
        )

    def _dashboard_kpi(self):
        request = self.factory.get("/game/")
        request.user = self.user
        response = DashboardView.as_view()(request)
        response.render()
        return response.context_data["kpi"]

    def test_dashboard_cache_is_invalidated_when_snapshot_updates(self):
        first_kpi = self._dashboard_kpi()
        self.assertEqual(first_kpi["gold"], 100)
        self.assertEqual(first_kpi["income"], 35)

        response = self.client.post(
            "/api/agent/snapshots/",
            data=json.dumps(
                {
                    "game_account_id": str(self.game_account.pk),
                    "base_snapshot": {
                        "gold": 999,
                        "income": 80,
                        "upkeep": -20,
                        "scientists_upkeep": -10,
                    },
                    "cities": [],
                    "military": {},
                }
            ),
            content_type="application/json",
            HTTP_X_AGENT_TOKEN="test-agent-token",
        )

        self.assertEqual(response.status_code, 200)

        second_kpi = self._dashboard_kpi()
        self.assertEqual(second_kpi["gold"], 999)
        self.assertEqual(second_kpi["income"], 50)


class DashboardHistoryLazyLoadTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="history-tester",
            email="history@example.com",
            password="secret123",
        )
        node = Node.objects.create(name="agent-history")
        self.account = Account.objects.create(
            node=node,
            label="Lobby History",
            email="history-cache@example.com",
            password_enc="enc",
        )
        self.game_account = GameAccount.objects.create(
            account=self.account,
            lobby_account_id=654,
            server_id="s6-br",
            server_language="br",
            server_number=6,
            name="HistoryPlayer",
        )
        AccountSnapshot.objects.create(
            account=self.account,
            game_account=self.game_account,
            base_snapshot={"gold": 1000, "income": 100, "upkeep": -25, "scientists_upkeep": -5},
            cities=[],
            military={},
        )
        AccountSnapshotHistory.objects.create(
            account=self.account,
            game_account=self.game_account,
            base_snapshot={"gold": 1000, "income": 100, "upkeep": -25, "scientists_upkeep": -5},
            cities=[],
            military={},
            captured_at=timezone.now(),
        )

    def test_dashboard_initial_context_does_not_embed_history(self):
        request = self.factory.get("/game/")
        request.user = self.user
        response = DashboardView.as_view()(request)
        response.render()

        self.assertEqual(response.context_data["kpi_history"], {})

    def test_dashboard_history_endpoint_returns_history_payload(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("game:dashboard-history"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn(str(self.game_account.pk), payload["history"])
