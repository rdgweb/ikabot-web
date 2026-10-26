"""N-163: validity by mission, the campaign's pass, players already covered and targets by value."""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.espionage.models import SpyReport
from apps.espionage.services.validity import ttl_hours
from apps.worldintel.models import WorldDump, WorldDumpCity, WorldDumpIsland

OK = "Sucesso"


class ValidityTests(TestCase):
    def test_by_mission(self):
        self.assertEqual(ttl_hours(26, {}, True), 15 * 24)
        self.assertEqual(ttl_hours(6, {}, True), 5 * 24)
        self.assertEqual(ttl_hours(5, {"wood": 150_000, "marble": 60_000}, True), 4 * 24)   # rich (>= 200.000)
        self.assertEqual(ttl_hours(5, {"wood": 1_000}, True), 8 * 24)                        # poor
        self.assertEqual(ttl_hours(5, {}, False), 48)                                        # failed: read nothing
        self.assertEqual(ttl_hours(3, {}, True), 48)


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
class TargetsTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(name="agente", transport="https")
        account = Account.objects.create(node=self.node, label="Conta", email="c@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br",
                                             server_language="br", server_number=78, name="Espiao")
        self.dump = WorldDump.objects.create(account=account, game_account=self.ga, captured_at=timezone.now())
        island = WorldDumpIsland.objects.create(dump=self.dump, island_id="9", name="Ilha", x=50, y=50)
        # Fulano: two cities; Rico: one rich city; Novo: never read; Pobre: read poor
        for cid, owner in (("101", "Fulano"), ("102", "Fulano"), ("201", "Rico"), ("301", "Novo"), ("401", "Pobre")):
            WorldDumpCity.objects.create(dump=self.dump, island=island, type="city", state="inactive", game_city_id=cid,
                                         owner_id=owner.lower(), owner_name=owner, name=f"C{cid}")
        self.pass_start = timezone.now() - timedelta(days=10)

    def report(self, cid, owner, mission, *, days_ago, data=None, valid=False):
        r = SpyReport.objects.create(game_account=self.ga, report_id=f"{cid}-{mission}-{days_ago}", target_city_id=cid,
                                     target_owner=owner, target_owner_id=owner.lower(), mission_id=mission,
                                     result_status=OK, data_json=data or {})
        when = timezone.now() - timedelta(days=days_ago)
        SpyReport.objects.filter(pk=r.pk).update(created_at=when,
                                                 expires_at=timezone.now() + timedelta(days=1 if valid else -1))

    def targets(self, **extra):
        params = {"skip_if_valid": "1", "missions": "5,6,26", "limit": "200", "game_account_id": str(self.ga.pk), **extra}
        r = self.client.get("/api/agent/worldintel/spy-targets/", params, HTTP_X_AGENT_TOKEN="test-agent-token",
                            HTTP_X_AGENT_NODE_ID=str(self.node.pk))
        self.assertEqual(r.status_code, 200, r.content[:300])
        return r.json()["targets"]

    def test_a_player_covered_in_this_pass_does_not_fill_the_list(self):
        for cid in ("101", "102"):                      # stock and garrison valid; only Invenções (per player) missing
            self.report(cid, "Fulano", 5, days_ago=1, valid=True, data={"wood": 10})
            self.report(cid, "Fulano", 6, days_ago=1, valid=True)
        self.assertIn("101", [t["game_city_id"] for t in self.targets()])
        ids = [t["game_city_id"] for t in self.targets(covered_owners="name:fulano")]
        self.assertNotIn("101", ids)
        self.assertNotIn("102", ids)

    def test_read_in_this_pass_counts_even_after_it_expires_except_a_rich_city(self):
        for cid, owner, stock in (("401", "Pobre", {"wood": 1_000}), ("201", "Rico", {"wood": 900_000})):
            self.report(cid, owner, 5, days_ago=5, data=stock)            # expired, but read in this pass
            self.report(cid, owner, 6, days_ago=5)
            self.report(cid, owner, 26, days_ago=5)
        ids = [t["game_city_id"] for t in self.targets(pass_since=self.pass_start.isoformat())]
        self.assertNotIn("401", ids)                                     # poor: done for this pass
        self.assertIn("201", ids)                                        # rich: read again when it expires

    def test_value_first(self):
        self.report("201", "Rico", 5, days_ago=20, data={"wood": 900_000})
        self.report("401", "Pobre", 5, days_ago=20, data={"wood": 1_000})
        order = [t["game_city_id"] for t in self.targets(prioritize="1")]
        self.assertEqual(order[0], "201")                                # rich first
        self.assertLess(order.index("301"), order.index("401"))          # never read before poor
        first = self.targets(prioritize="1")[0]
        self.assertEqual((first["priority"], first["last_stock"]), (0, 900_000))

    def test_the_parameters_can_come_in_the_body(self):
        # 188 covered players do not fit in a URL: the agent posts them
        for cid in ("101", "102"):
            self.report(cid, "Fulano", 5, days_ago=1, valid=True, data={"wood": 10})
            self.report(cid, "Fulano", 6, days_ago=1, valid=True)
        owners = ",".join(["name:fulano"] + [f"name:jogador{n:04d}" for n in range(400)])
        r = self.client.post("/api/agent/worldintel/spy-targets/",
                             {"skip_if_valid": "1", "missions": "5,6,26", "limit": 200, "game_account_id": str(self.ga.pk),
                              "covered_owners": owners, "prioritize": "1"},
                             content_type="application/json", HTTP_X_AGENT_TOKEN="test-agent-token",
                             HTTP_X_AGENT_NODE_ID=str(self.node.pk))
        self.assertEqual(r.status_code, 200, r.content[:300])
        ids = [t["game_city_id"] for t in r.json()["targets"]]
        self.assertNotIn("101", ids)
        self.assertIn("301", ids)
