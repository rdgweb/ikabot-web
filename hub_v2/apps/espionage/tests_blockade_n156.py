"""N-156: the rich target alert never suggests a blockade fleet that loses to the fleet in the port."""
from django.test import SimpleTestCase

from apps.espionage.api.views import _blockade_plan


class BlockadePlanTests(SimpleTestCase):
    def test_a_small_fleet_against_a_full_port_cannot_win(self):
        own = {"Aríete a Vapor": 16, "Lança-Chamas": 1, "Lança-Foguetes": 6}
        port = {210: 811, 213: 200, 216: 101, 215: 95, 211: 59, 217: 30, 212: 30}      # VIP8, 20/10
        plan = _blockade_plan(own, enemy_fleet=port, enemy_upgrades={}, city_level=35)
        self.assertIs(plan["can_win"], False)

    def test_a_big_fleet_against_a_small_port_wins(self):
        own = {"Aríete a Vapor": 200, "Lança-Chamas": 200, "Lança-Foguetes": 100}
        plan = _blockade_plan(own, enemy_fleet={210: 5}, enemy_upgrades={}, city_level=10)
        self.assertIs(plan["can_win"], True)
        self.assertTrue(plan["fleet"])

    def test_without_ships_in_the_port_the_rule_suggests_and_nothing_is_judged(self):
        plan = _blockade_plan({"Aríete a Vapor": 10}, enemy_fleet={}, enemy_upgrades={})
        self.assertIsNone(plan["can_win"])

    def test_without_a_fleet_of_ours_against_ships_in_the_port(self):
        self.assertEqual(_blockade_plan({}, enemy_fleet={210: 5}), {"fleet": {}, "can_win": False})


class BalloonCarrierPortTests(SimpleTestCase):
    """The port of Fla 2 had only balloon carriers: the guess answered with our carriers, they lost, and the alert
    suggested the whole fleet (1.662 ships)."""

    def test_carriers_are_answered_by_front_ships_and_the_fleet_stays_small(self):
        from apps.espionage.api.views import _ID_TO_NAME, _blockade_plan

        own = {216: 417, 210: 540, 215: 48, 217: 12, 212: 40, 218: 467, 219: 101, 220: 37}
        plan = _blockade_plan({_ID_TO_NAME[k]: v for k, v in own.items()}, {219: 70})
        self.assertTrue(plan["can_win"])
        self.assertNotIn(219, plan["fleet"])
        self.assertLess(sum(plan["fleet"].values()), 200)
