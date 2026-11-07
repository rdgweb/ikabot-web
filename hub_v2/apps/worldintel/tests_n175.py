"""N-174/N-175: players marked as possible multi accounts (left out of spying) or as piracy targets, the suggestions,
and the Alvos page."""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.orgs.models import Membership, Organization

from .models import PlayerMark, WorldDump, WorldDumpCity, WorldDumpIsland, WorldDumpPlayer
from .services.marks import suggestions


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
class PlayerMarkTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model()(username="dono")
        self.user._own_organization = True
        self.user.set_password("x")
        self.user.save()
        self.org = Organization.objects.create(name="Org", slug="org")
        Membership.objects.create(organization=self.org, user=self.user, role=Membership.OWNER)
        self.node = Node.objects.create(name="agente", transport="https", organization=self.org)
        account = Account.objects.create(node=self.node, label="Conta", email="c@example.com", password_enc="x",
                                         organization=self.org)
        self.ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br",
                                             server_language="br", server_number=78, name="Espiao")
        self.dump = WorldDump.objects.create(account=account, game_account=self.ga, status="complete",
                                             captured_at=timezone.now())
        # Grande lives on islands 1 and 2; Apoio (tiny, same alliance) has both its cities there; Solto lives apart
        for island_id, x in (("1", 10), ("2", 11), ("3", 40)):
            WorldDumpIsland.objects.create(dump=self.dump, island_id=island_id, x=x, y=10)
        islands = {i.island_id: i for i in WorldDumpIsland.objects.filter(dump=self.dump)}
        cities = [("g1", "1", "Grande", "100", ""), ("g2", "2", "Grande", "100", ""), ("a1", "1", "Apoio", "200", "inactive"),
                  ("a2", "2", "Apoio", "200", "inactive"), ("s1", "3", "Solto", "300", "inactive")]
        for pos, (cid, island, owner, owner_id, state) in enumerate(cities):
            WorldDumpCity.objects.create(dump=self.dump, island=islands[island], position=pos, game_city_id=cid,
                                         type="city", state=state, owner_name=owner, owner_id=owner_id, ally_tag="TAG")
        for owner_id, owner, score in (("100", "Grande", 5_000_000), ("200", "Apoio", 50_000), ("300", "Solto", 40_000)):
            WorldDumpPlayer.objects.create(dump=self.dump, owner_id=owner_id, owner_name=owner, ally_tag="TAG",
                                           building_score=score)
        self.client.force_login(self.user)

    def _mark(self, owner, kind="multi", **extra):
        return self.client.post("/intel/players/mark/", {"dump": str(self.dump.pk), "kind": kind, "owner_name": owner,
                                                         "next": "/intel/targets/?view=multi", **extra})

    def test_suggestion_mark_and_unmark(self):
        found = suggestions(self.dump, self.org)
        self.assertEqual([(s["owner_name"], s["main_owner_name"], s["same_ally"]) for s in found], [("Apoio", "Grande", True)])
        self.assertEqual(self._mark("Apoio", main_owner_name="Grande").status_code, 302)
        self.assertEqual(PlayerMark.objects.get().main_owner_name, "Grande")
        self.assertEqual(suggestions(self.dump, self.org), [])
        self._mark("Apoio")
        self.assertFalse(PlayerMark.objects.exists())

    def test_a_multi_account_is_not_spied_and_the_agent_sees_the_marks(self):
        self._mark("Apoio")
        self._mark("Solto", kind="pirate_target")
        r = self.client.get("/api/agent/worldintel/spy-targets/", {"missions": "5", "limit": "50",
                            "game_account_id": str(self.ga.pk), "only_inactive": "1"},
                            HTTP_X_AGENT_TOKEN="test-agent-token", HTTP_X_AGENT_NODE_ID=str(self.node.pk))
        ids = [t["game_city_id"] for t in r.json()["targets"]]
        self.assertEqual(ids, ["s1"])
        marks = self.client.get("/api/agent/worldintel/player-marks/", {"game_account_id": str(self.ga.pk)},
                                HTTP_X_AGENT_TOKEN="test-agent-token", HTTP_X_AGENT_NODE_ID=str(self.node.pk)).json()
        self.assertEqual(marks, {"multi": ["Apoio"], "pirate_target": ["Solto"]})

    def test_the_targets_page_and_the_player_card(self):
        self._mark("Apoio")
        for view in ("raid", "pirate", "multi"):
            page = self.client.get(f"/intel/targets/?view={view}&dump={self.dump.pk}")
            self.assertEqual(page.status_code, 200, view)
        self.assertContains(self.client.get(f"/intel/targets/?view=multi&dump={self.dump.pk}"), "Apoio")
        card = self.client.get(f"/intel/players/detail/?owner_name=Apoio&dump={self.dump.pk}")
        self.assertContains(card, "Desmarcar")
