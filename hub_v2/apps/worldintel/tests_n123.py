"""N-123: saving a world dump batch stays cheap — the fresh city states reach only the older dumps of the same
server and organization — and a retry of the job that starts a dump continues it instead of opening another."""
import json

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.jobs.models import Job
from apps.orgs.models import Organization

from .models import WorldDump, WorldDumpCity, WorldDumpIsland


def _island(island_id, cities):
    return {"island_id": island_id, "name": f"Ilha {island_id}", "x": 10, "y": 20,
            "cities": [{"id": cid, "position": pos, "name": f"Cidade {cid}", "owner_id": "77", "owner_name": "Fulano",
                        "type": "city", "state": state} for pos, (cid, state) in enumerate(cities)]}


@override_settings(AGENT_TOKEN="", AGENT_ALLOWED_IPS="")
class WorldDumpSaveTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(name="agente", transport="https")
        self.key = self.node.new_agent_token
        self.account = Account.objects.create(node=self.node, label="Conta", email="c@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=self.account, lobby_account_id=1, server_id="s78-br",
                                             server_language="br", server_number=78, name="Atenas")
        self.other_world = GameAccount.objects.create(account=self.account, lobby_account_id=2, server_id="s12-br",
                                                      server_language="br", server_number=12, name="Esparta")
        other_org = Organization.objects.create(name="Outra", slug="outra")
        other_node = Node._base_manager.create(name="outro", transport="https", organization=other_org)
        other_account = Account._base_manager.create(node=other_node, label="Outra", email="o@example.com",
                                                     password_enc="x", organization=other_org)
        self.other_org_ga = GameAccount._base_manager.create(account=other_account, lobby_account_id=3, server_id="s78-br",
                                                             server_language="br", server_number=78, name="Tebas")

    def _old_dump(self, ga, state="inactive"):
        dump = WorldDump._base_manager.create(account=ga.account, game_account=ga, status="complete",
                                              captured_at=timezone.now())
        island = WorldDumpIsland._base_manager.create(dump=dump, island_id="500")
        WorldDumpCity._base_manager.create(dump=dump, island=island, game_city_id="9001", type="city", state=state)
        return dump

    def _post(self, path, payload):
        return self.client.post(path, json.dumps(payload), content_type="application/json",
                                HTTP_X_AGENT_TOKEN=self.key, HTTP_X_AGENT_NODE_ID=str(self.node.pk))

    def _create(self, islands, source_job=None, dump_status="in_progress"):
        payload = {"account_id": str(self.account.pk), "game_account_id": str(self.ga.pk), "scope_mode": "region",
                   "dump_status": dump_status, "islands": islands}
        if source_job is not None:
            payload["source_job_id"] = str(source_job.pk)
        return self._post("/api/agent/world-dumps/", payload)

    def _state_of(self, dump):
        return WorldDumpCity._base_manager.get(dump=dump, game_city_id="9001").state

    def test_fresh_states_reach_only_the_same_server_and_organization_in_few_queries(self):
        same = self._old_dump(self.ga)
        other_world = self._old_dump(self.other_world)
        other_org = self._old_dump(self.other_org_ga)
        cities = [("9001", "vacation")] + [(str(9100 + i), "inactive") for i in range(40)]
        with CaptureQueriesContext(connection) as queries:
            response = self._create([_island("500", cities)])
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self._state_of(same), "vacation")
        self.assertEqual(self._state_of(other_world), "inactive")
        self.assertEqual(self._state_of(other_org), "inactive")
        updates = [q for q in queries.captured_queries if q["sql"].startswith("UPDATE") and "worlddumpcity" in q["sql"]]
        self.assertLessEqual(len(updates), 2)                       # one per state, never one per city

    def test_a_retry_of_the_first_batch_continues_the_same_dump(self):
        first = Job.objects.create(account=self.account, game_account=self.ga, node=self.node, action_code=602,
                                   status="error", inputs_json="{}", timeout_sec=1800)
        retry = Job.objects.create(account=self.account, game_account=self.ga, node=self.node, action_code=602,
                                   status="running", inputs_json="{}", timeout_sec=1800,
                                   root_job_id=first.pk, source_job_id=first.pk)
        islands = [_island("500", [("9001", "")]), _island("501", [("9002", "")])]
        dump_id = self._create(islands, source_job=first).json()["dump_id"]
        again = self._create(islands, source_job=retry).json()
        self.assertEqual(again["dump_id"], dump_id)
        self.assertEqual(WorldDump._base_manager.count(), 1)
        self.assertEqual((again["island_count"], WorldDumpCity._base_manager.count()), (2, 2))

    def test_another_run_opens_its_own_dump(self):
        first = Job.objects.create(account=self.account, game_account=self.ga, node=self.node, action_code=602,
                                   status="finished", inputs_json="{}", timeout_sec=1800)
        other = Job.objects.create(account=self.account, game_account=self.ga, node=self.node, action_code=602,
                                   status="running", inputs_json="{}", timeout_sec=1800)
        one = self._create([_island("500", [("9001", "")])], source_job=first).json()["dump_id"]
        two = self._create([_island("500", [("9001", "")])], source_job=other).json()["dump_id"]
        self.assertNotEqual(one, two)
