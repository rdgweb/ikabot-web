"""N-165: with "Fé em 100% automática" on, a temple reading that finds an island we worship under 100% is followed by
the account's priests; islands without our priests are left alone."""
import json

from apps.accounts.models import Account
from apps.game.models import AccountSnapshot
from apps.game.services.temples import auto_balance_after_reading, faith_settings, save_faith_settings
from apps.orgs.services import default_organization

from .models import Job
from .tests_n141 import TempleAccounts


class AutoFaithTests(TempleAccounts):
    def setUp(self):
        super().setUp()
        self.org = default_organization()
        Account.objects.filter(pk__in=[ga.account_id for ga in self.gas]).update(organization=self.org)
        for snap in AccountSnapshot.objects.all():                    # the other player has less: the island is at 93.7%
            wonder = snap.base_snapshot["island_wonders"]["4479"]
            wonder["participants"][0]["island_share_percent"] = 70.0
            wonder["faith_percent"] = 93.0
            snap.save(update_fields=["base_snapshot"])
        for ga in self.gas:
            ga.refresh_from_db()

    def test_off_by_default(self):
        self.assertEqual(faith_settings(self.org), {"auto_faith": False, "faith_reserve": 0})
        self.assertEqual(auto_balance_after_reading(self.gas[0]), 0)

    def test_on_the_account_gets_more_priests(self):
        save_faith_settings(self.org, auto=True, reserve=100)
        self.assertEqual(auto_balance_after_reading(self.gas[0]), 1)
        job = Job.objects.get(action_code=35, game_account=self.gas[0])
        inputs = json.loads(job.inputs_json)
        self.assertEqual(inputs["target_mode"], "city_targets")
        self.assertGreater(inputs["city_targets"]["11"], 287)                      # Glitnir: more priests
        self.assertEqual(inputs["reserve_citizens"], 100)
        self.assertEqual(auto_balance_after_reading(self.gas[0]), 0)                 # one adjustment at a time

    def test_an_island_without_our_priests_is_left_alone(self):
        save_faith_settings(self.org, auto=True, reserve=0)
        for snap in AccountSnapshot.objects.all():
            for city in snap.cities:
                city["miracle_state"]["priests"] = 0
                city["miracle_state"]["island_conversion_percent"] = 0
            snap.save(update_fields=["cities"])
        self.assertEqual(auto_balance_after_reading(self.gas[0]), 0)

    def test_the_switch_on_the_temples_page(self):
        from apps.orgs.models import Membership

        Membership.objects.get_or_create(organization=self.org, user=self.user, defaults={"role": Membership.OWNER})
        self.client.post("/game/temples/", {"action": "auto_faith", "auto_faith": "1", "faith_reserve": "50"})
        self.assertEqual(faith_settings(self.org), {"auto_faith": True, "faith_reserve": 50})
        self.assertIn("Fé em 100% automática", self.client.get("/game/temples/").content.decode())


class FaithMarginTests(AutoFaithTests):
    def test_100_is_aimed_with_the_margin_and_a_lower_target_is_kept(self):
        from apps.game.services.temples import FAITH_TARGET, faith_goal

        self.assertEqual((FAITH_TARGET, faith_goal(100), faith_goal(80)), (103.0, 103.0, 80.0))
