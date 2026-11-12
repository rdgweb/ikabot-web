"""N-179: Roubar Bárbaros — the form lists the cities with their troops and the account's islands, and the job carries
what was chosen."""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot


class BarbariansFormTests(TestCase):
    def setUp(self):
        from apps.orgs.models import Membership
        from apps.orgs.services import default_organization

        user = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        Membership.objects.get_or_create(organization=default_organization(), user=user, defaults={"role": Membership.OWNER})
        self.client.force_login(user)
        node = Node.objects.create(name="agente", transport="https")
        account = Account.objects.create(node=node, label="Conta", email="c@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br",
                                             server_language="br", server_number=78, name="HAVIT")
        military = {"by_city": [{"city_id": 63355, "troops": {"Espadachim": 90, "Carabineiro": 21}, "fleet": {}}]}
        cities = [{"id": 63355, "name": "lll3lll", "island_id": 2488, "x": 83, "y": 77, "tradegood": 2},
                  {"id": 63388, "name": "lll4lll", "island_id": 146, "x": 53, "y": 43, "tradegood": 4}]
        AccountSnapshot.objects.create(account=account, game_account=self.ga, military=military, base_snapshot={}, cities=cities)

    def test_the_form_lists_cities_troops_and_islands(self):
        html = self.client.get("/jobs/new/form/", {"ga": str(self.ga.pk), "action": "19"}).content.decode()
        self.assertIn("Tropas do ataque", html)
        self.assertIn("maxLevel: 9,", html)                              # past 9 the barbarians take revenge
        data = json.loads(html.split('id="barbarians-ui-data" type="application/json">')[1].split("</script>")[0])
        self.assertEqual(data["cities"][0]["name"], "lll3lll")
        self.assertEqual({t["id"]: t["have"] for t in data["cities"][0]["troops"]}, {302: 90, 304: 21})
        self.assertEqual({i["id"]: i["resource"] for i in data["islands"]}, {"2488": "Mármore", "146": "Enxofre"})
        self.assertNotIn("Tropas personalizadas (JSON)", html)          # no raw field left
        self.assertNotIn("Cidade que ataca em cada ilha (JSON)", html)

    def test_the_job_carries_the_choice(self):
        from apps.jobs.models import Job

        self.client.post("/jobs/new/submit/", {
            "game_account": str(self.ga.pk), "action_code": "19", "city_id": "63355", "island_ids": "2488,146", "island_cities": json.dumps({"2488": "63355", "146": "63388"}),
            "troop_mode": "custom", "custom_troops": json.dumps({"302": 60}), "loot": "false", "min_barb_level": "3",
            "max_barb_level": "20", "min_resources": "10000", "extra_ships": "2", "recheck_minutes": "15", "max_random_wait": "30"})
        inputs = json.loads(Job.objects.get(action_code=19).inputs_json)
        self.assertEqual((str(inputs["city_id"]), inputs["island_ids"], inputs["troop_mode"]), ("63355", "2488,146", "custom"))
        self.assertEqual(json.loads(inputs["custom_troops"]), {"302": 60})
        self.assertFalse(inputs["loot"])
        self.assertEqual(json.loads(inputs["island_cities"]), {"2488": "63355", "146": "63388"})
        self.assertEqual((inputs["min_barb_level"], inputs["min_resources"]), (3, 10000))

    def test_the_job_page_shows_what_each_island_gave(self):
        from apps.jobs.models import Job

        state = {"4477": {"level": 4, "status": "rebuilding", "attacks": 3, "loots": 1, "looted": 2_000, "gold": 900,
                          "village_resources": 950, "village_gold": 300, "last_ships": 2, "attacked_at": 1794000000,
                          "last_troops": {"303": 21}}}
        job = Job.objects.create(account=self.ga.account, game_account=self.ga, node=self.ga.account.node, action_code=19,
                                 status="scheduled", timeout_sec=60,
                                 inputs_json=json.dumps({"island_ids": "4477", "islands_state": state, "loot_total": 2_000,
                                                         "gold_total": 900, "total_attacks": 3, "total_loots": 1}))
        html = self.client.get(f"/jobs/{job.pk}/").content.decode()
        self.assertIn("Recursos roubados", html)
        self.assertIn("em reconstrução", html)
        self.assertIn("2.000", html)
