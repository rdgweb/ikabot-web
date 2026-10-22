"""N-154: Dispensar Unidades — the form lists each city's troops and ships, and the job carries what was chosen."""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot


class DismissUnitsFormTests(TestCase):
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
        military = {"by_city": [{"city_id": 63355, "troops": {"Fundeiro": 12, "Lanceiro": 3}, "fleet": {"Aríete a Vapor": 2}},
                                {"city_id": 63388, "troops": {"Hoplita": 44}, "fleet": {}}]}
        cities = [{"id": 63355, "name": "lll3lll"}, {"id": 63388, "name": "lll4lll"}]
        AccountSnapshot.objects.create(account=account, game_account=self.ga, military=military, base_snapshot={}, cities=cities)

    def test_the_form_lists_each_city_units(self):
        html = self.client.get("/jobs/new/form/", {"ga": str(self.ga.pk), "action": "1204"}).content.decode()
        self.assertIn("Dispensar unidades", html)
        data = json.loads(html.split('id="dismiss-ui-data" type="application/json">')[1].split("</script>")[0])
        by_city = {c["name"]: {u["id"]: u["have"] for u in c["units"]} for c in data["cities"]}
        self.assertEqual(by_city, {"lll3lll": {301: 12, 315: 3, 216: 2}, "lll4lll": {303: 44}})
        self.assertIn("Não tem volta", html)

    def test_the_job_carries_the_choice_and_the_confirmation(self):
        from apps.jobs.models import Job

        self.client.post("/jobs/new/submit/", {"game_account": str(self.ga.pk), "action_code": "1204",
                                                "dismiss": json.dumps({"63388": {"303": 10}}), "confirm": "on"})
        inputs = json.loads(Job.objects.get(action_code=1204).inputs_json)
        self.assertEqual(json.loads(inputs["dismiss"]), {"63388": {"303": 10}})
        self.assertTrue(inputs["confirm"])


class MaintainSurplusFormTests(DismissUnitsFormTests):
    """N-159: the Manter form has the surplus card and its choice reaches the job."""

    def test_the_form_has_the_surplus_card(self):
        html = self.client.get("/jobs/new/form/", {"ga": str(self.ga.pk), "action": "1005"}).content.decode()
        self.assertIn("Juntar e dispensar o resto", html)
        self.assertIn('name="surplus_mode"', html)

    def test_the_choice_reaches_the_job(self):
        from apps.jobs.models import Job

        self.client.post("/jobs/new/submit/", {
            "game_account": str(self.ga.pk), "action_code": "1005", "building_type": "troops", "mode": "maintain",
            "maintain_scope": "selected_uniform", "city_ids": ["63355", "63388"], "units_303": "50",
            "surplus_mode": "gather_dismiss", "surplus_min_pct": "25", "consolidate_city_id": "63388",
            "recheck_minutes": "120"})
        inputs = [json.loads(j.inputs_json) for j in Job.objects.filter(action_code=1005)]
        self.assertTrue(inputs, "nenhum job criado")
        self.assertTrue(all(i.get("surplus_mode") == "gather_dismiss" and i.get("surplus_min_pct") == 25
                            and str(i.get("consolidate_city_id")) == "63388" for i in inputs), inputs)
