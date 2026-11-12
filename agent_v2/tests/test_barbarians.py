"""N-179: Roubar Bárbaros, as the game showed it — each island's village attacked from a town of that island (troops
weigh nothing there), merchant ships for the loot, and the game's own form fields."""
import unittest

from game_client.actions.barbarians import AttackBarbarianVillageAction, loot_ships_for_level
from runners.barbarians import BarbariansRunner, _choose_troops, _loot_unit

VIEW_HTML = """
<input type="hidden" name="cargo_army_303_upkeep" value="3"><input type="hidden" name="cargo_army_315_upkeep" value="1">
<script>new missionController(
    37,
    500,
    300, null, 0, 90, false);
var s = ikariam.controller.sliders["slider_303"]; s.upkeep = 2.58; s.weight = 0; s.unitJourneyTime = 40;
var s = ikariam.controller.sliders["slider_315"]; s.upkeep = 0.86; s.weight = 0; s.unitJourneyTime = 40;
</script>""" + " " * 200


class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class _Client:
    _server_url = "https://s78-br.ikariam.gameforge.com/index.php"

    def __init__(self):
        self._action_request = "t0"
        self.session = None
        self.sent = []

    def _request(self, method, url, **kwargs):
        if method == "GET":
            return _Resp([["updateGlobalData", {"actionRequest": "t1"}], ["changeView", ["plunder", VIEW_HTML]]])
        self.sent.append(dict(kwargs["data"]))
        return _Resp([["updateGlobalData", {"actionRequest": "t2"}], ["provideFeedback", [{"type": 10, "text": "ok"}]]])


class AttackTests(unittest.TestCase):
    def test_the_form_the_game_expects_and_ships_for_the_loot(self):
        client = _Client()
        result = AttackBarbarianVillageAction(client).execute(from_city_id=63388, island_id=4478, troops={303: 20, 315: 7},
                                                              transporters=50)
        sent = client.sent[0]
        self.assertEqual((sent["function"], sent["destinationCityId"], sent["islandId"]), ("attackBarbarianVillage", "0", "4478"))
        self.assertEqual((sent["cargo_army_303"], sent["cargo_army_303_upkeep"], sent["cargo_army_315_upkeep"]), (20, "3", "1"))
        self.assertEqual(sent["actionRequest"], "t1")          # the token of the screen just opened
        self.assertEqual(result["transporters"], 37)            # 50 wanted, 37 free

    def test_loot_ships_grow_with_the_level(self):
        self.assertEqual(loot_ships_for_level(1), 2)
        self.assertGreater(loot_ships_for_level(33), 150)
        self.assertLess(loot_ships_for_level(20), loot_ships_for_level(30))


class TroopTests(unittest.TestCase):
    def test_low_levels_send_what_fights_and_high_levels_need_the_table(self):
        have = {303: 20, 315: 7, 301: 7, 306: 12, 310: 3}
        self.assertEqual(_choose_troops("schematic", 1, "", have), {303: 7})                 # 1 scout: 2*1+5
        self.assertEqual(_choose_troops("schematic", 9, "", {303: 4502, 302: 2519}), {303: 105})   # 50 scouts
        self.assertIsNone(_choose_troops("schematic", 9, "", {303: 30, 315: 7}))             # 37 against 50 scouts
        self.assertEqual(_choose_troops("available", 1, "", have), {303: 20, 315: 7, 301: 7, 306: 12})
        self.assertIsNone(_choose_troops("schematic", 12, "", have))           # the table asks for units it lacks
        self.assertEqual(_choose_troops("custom", 12, '{"303": 10}', have), {303: 10})
        self.assertEqual(_loot_unit(have), {315: 1})


class _Hub:
    def __init__(self):
        self.logs = []

    def report_log(self, job_id, level, msg):
        self.logs.append(msg)


class RoundTests(unittest.TestCase):
    def _runner(self):
        runner = BarbariansRunner.__new__(BarbariansRunner)
        runner.hub = _Hub()
        runner._get_available_troops = lambda jid, client, town: {303: 20, 315: 7}
        return runner

    def test_attack_with_ships_for_what_the_village_has_then_collect_until_empty_and_count_the_loot(self):
        import runners.barbarians as module

        runner, sent = self._runner(), []
        village = {"total": 1_200, "gold": 450}
        original = module.fetch_village_info
        module.fetch_village_info = lambda client, island_id: dict(village)

        class Game:
            barb = {"level": 3, "destroyed": False, "under_attack": False}

            def get_barbarian_state(self, island_id):
                return dict(self.barb)

            def attack_barbarian_village(self, **kw):
                sent.append(kw)
                return {"transporters": kw["transporters"], "free_ships": 40}

        try:
            game, st = Game(), {}
            totals = {"total_attacks": 0, "total_loots": 0, "loot_total": 0, "gold_total": 0}
            args = ("schematic", "", True, 1, 9, 0, 0)
            runner._island_round("j", game, "4478", "63388", st, totals, *args)
            self.assertEqual((sent[0]["from_city_id"], sent[0]["troops"], sent[0]["transporters"]), ("63388", {303: 15}, 3))
            Game.barb = {"level": 4, "destroyed": True, "under_attack": False}
            village.update(total=300)                                   # 900 came back with the attack
            runner._island_round("j", game, "4478", "63388", st, totals, *args)
            self.assertEqual((sent[1]["troops"], sent[1]["transporters"]), ({315: 1}, 1))
            village.update(total=0)                                     # the collection took the rest
            runner._island_round("j", game, "4478", "63388", st, totals, *args)
            self.assertEqual(len(sent), 2)
            self.assertEqual((totals["loot_total"], totals["gold_total"], totals["total_attacks"], totals["total_loots"]),
                             (1_200, 450, 1, 1))
            Game.barb = {"level": 4, "destroyed": False, "under_attack": True}
            runner._island_round("j", game, "4478", "63388", st, totals, *args)
            self.assertEqual(len(sent), 2)                              # the battle is on
        finally:
            module.fetch_village_info = original


if __name__ == "__main__":
    unittest.main()
