"""N-143: the shrine reading ties each worship slot to its city, and the loop keeps the worshipped gods in
grace, giving favor only to the ones whose grace ended and coming back when the next one ends."""
import json
import unittest
from unittest.mock import MagicMock

from game_client.actions.shrine import ShrineAction, grace_seconds
from runners.economy import ActivateShrineLoopRunner

CSS = {1: "pan", 2: "dionysus", 3: "tyche", 4: "plutus", 5: "theia", 6: "hephaestos"}


def _payload(graces, slots, worshipped):
    """graces {god_id: "2D 23h"|"-"}, slots [(city_id, god_id)] -> the ajax answer of the shrine overview."""
    template = {"cityId": 7, "position": 6, "currentFavor": 1275, "researchedGods": [1, 2, 3, 4, 5, 6],
                "load_js": {"params": json.dumps({"godsWorshipped": {str(g): g in worshipped for g in CSS}})}}
    for god_id, css in CSS.items():
        template[f"shrineOfOlympus .god_{css} .gracePeriod"] = graces.get(god_id, "-")
    selects = ""
    for number, (city_id, god_id) in enumerate(slots, start=1):
        template[f"shrineOfOlympus .gods.worshiping li[data-number={number}] .god"] = {"class": f"god god_{CSS[god_id]}"}
        template[f"shrineOfOlympus .gods.worshiping li[data-number={number}] .currentBonus"] = "57,00"
        selects += (f'<select class="dropdown js_citySelect"\n        data-number="{number}"\n'
                    f'        data-known-city-id="{city_id}"></select>')
    html = f'<div id="tabWorship">{selects}</div>'
    return [["updateTemplateData", template], ["changeView", ["shrineOfOlympus", html]]]


class ShrineReadingTests(unittest.TestCase):
    def test_grace_text(self):
        self.assertEqual(grace_seconds("2D 23h"), 2 * 86400 + 23 * 3600)
        self.assertEqual(grace_seconds("21h 46m"), 21 * 3600 + 46 * 60)
        self.assertEqual(grace_seconds("-"), 0)

    def test_each_slot_has_its_city_and_god(self):
        state = ShrineAction._parse_state_payload(_payload({3: "21h 46m"}, [(101, 3), (102, 2)], {2, 3}))
        slots = {w["slot"]: (w["city_id"], w["god_id"]) for w in state["worship_assignments"]}
        self.assertEqual(slots, {1: (101, 3), 2: (102, 2)})
        self.assertEqual(state["gods_worshipped"], [2, 3])
        self.assertEqual(state["gods"][3]["grace_seconds"], 21 * 3600 + 46 * 60)
        self.assertEqual(state["gods"][2]["grace_seconds"], 0)


class ActionRequestTests(unittest.TestCase):
    def test_each_answer_keeps_the_next_action_request(self):
        client = MagicMock()
        client._action_request = "old"
        client._request.return_value.json.return_value = [["updateGlobalData", {"actionRequest": "new1"}], ["provideFeedback", []]]
        ShrineAction(client).activate_god(city_id=7, position=6, god_id=2)
        self.assertEqual(client._action_request, "new1")
        client._request.return_value.json.return_value = [["updateGlobalData", {"actionRequest": "new2"}]]
        ShrineAction(client).activate_god(city_id=7, position=6, god_id=3)
        self.assertEqual(client._request.call_args[1]["data"]["actionRequest"], "new1")    # the second donation used the fresh one


class ShrineLoopTests(unittest.TestCase):
    def _run(self, payload, inputs=None):
        runner = ActivateShrineLoopRunner.__new__(ActivateShrineLoopRunner)
        runner.log = MagicMock()
        runner.save_game_client = MagicMock()
        client = MagicMock()
        states = [ShrineAction._parse_state_payload(p) for p in payload]
        client.get_shrine_state.side_effect = states
        runner.get_or_login_game_client = MagicMock(return_value=client)
        context = {"jid": "j", "aid": "a", "ga_id": "g", "shrine": {"city_id": 7, "position": 6, "city_name": "Valhala"},
                   "selected_gods": [], "keep_worshipped": True, "only_expired": True, "creds": {},
                   "cycle_seconds": 72 * 3600, "favor_recheck_seconds": 1800, "inputs": inputs or {}}
        result, _before, _after = runner._run_activation_cycle(context)
        return runner, client, context, result

    def test_only_the_worshipped_god_out_of_grace_gets_favor(self):
        before = _payload({2: "2D 23h", 3: "21h 46m"}, [(101, 3), (102, 2), (103, 6)], {2, 3, 6})
        after = _payload({2: "2D 23h", 3: "21h 46m", 6: "3D"}, [(101, 3), (102, 2), (103, 6)], {2, 3, 6})
        runner, client, context, result = self._run([before, after])
        client.activate_shrine_god.assert_called_once_with(7, 6, 6)         # Hefesto only
        self.assertEqual(result["status"], "activated")
        self.assertEqual(runner._next_check_seconds(context, result), 21 * 3600 + 46 * 60 + 120)

    def test_all_in_grace_waits_for_the_first_to_end(self):
        state = _payload({2: "2D 23h", 3: "5h"}, [(101, 3), (102, 2)], {2, 3})
        runner, client, context, result = self._run([state])
        client.activate_shrine_god.assert_not_called()
        self.assertEqual(result["status"], "in_grace")
        self.assertEqual(runner._next_check_seconds(context, result), 5 * 3600 + 120)


class AlignShrineTests(unittest.TestCase):
    """Action 36: each city worships the god chosen for its luxury good."""

    def test_wrong_gods_change_and_a_new_city_takes_a_free_slot(self):
        from runners.economy import AlignShrineRunner

        runner = AlignShrineRunner.__new__(AlignShrineRunner)
        runner.log, runner.save_game_client, runner.hub = MagicMock(), MagicMock(), MagicMock()
        runner.resolve_credentials = MagicMock(return_value={"x": 1})
        snapshot = {"base_snapshot": {}, "military": {}, "cities": [
            {"id": 101, "name": "Vinho", "tradegood": 1, "buildings": [{"building": "shrineOfOlympus", "position": 6, "level": 20}]},
            {"id": 102, "name": "Marmore", "tradegood": 2},
            {"id": 103, "name": "Cristal", "tradegood": 3},
        ]}
        runner.hub.get_snapshot.return_value = snapshot
        before = ShrineAction._parse_state_payload(_payload({}, [(101, 3), (102, 3)], {3}))
        before["additional_cities_possible"] = 1
        before["slot_order"] = [101, 102, 0]
        after = ShrineAction._parse_state_payload(_payload({}, [(101, 2), (102, 3), (103, 4)], {2, 3, 4}))
        client = MagicMock()
        client.get_shrine_state.side_effect = [before, after]
        runner.get_or_login_game_client = MagicMock(return_value=client)
        result = runner.execute({"job_id": "j", "account_id": "a", "game_account_id": "g",
                                 "shrine_gods": {"1": 2, "2": 3, "3": 4, "4": 6}})          # crystal -> Pluto
        calls = [c.kwargs for c in client.change_god_worship.call_args_list]
        self.assertEqual(calls[0], {"old_city_id": 101, "new_city_id": 101, "god_id": 2, "order": [101, 102, 0]})
        self.assertEqual((calls[1]["old_city_id"], calls[1]["new_city_id"], calls[1]["god_id"], calls[1]["order"]), (0, 103, 4, [101, 102, 103]))
        self.assertEqual(len(calls), 2)                                                     # Marmore already on Tique
        self.assertTrue(result.success)


if __name__ == "__main__":
    unittest.main()
