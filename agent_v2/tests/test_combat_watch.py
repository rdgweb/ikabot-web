"""N-109: watching the list of combats and delivering only what is new to the hub."""

import unittest
from unittest.mock import MagicMock

from game_client.actions.military import FetchCombatDetailedReportAction, FetchCombatReportsAction
from services.combat_watch import watch_combats


class Hub:
    def __init__(self, finished=True, fail=False):
        self.finished, self.fail = finished, fail
        self.saved = []

    def save_combat_report(self, game_account_id, report):
        if self.fail:
            raise RuntimeError("hub offline")
        self.saved.append((game_account_id, report))
        return {"ok": True, "finished": self.finished}


class Client:
    """The game as the watcher sees it: a list of combats and, for each, its rounds."""

    def __init__(self, listed, rounds):
        self.listed, self.rounds = listed, rounds
        self.detail_calls, self.summary_calls = [], []

    def fetch_combat_reports(self, _city_id, limit=10):
        return [dict(item) for item in self.listed]

    def fetch_combat_detailed_report(self, _city_id, combat_id, known_upto=0):
        self.detail_calls.append((combat_id, known_upto))
        total = self.rounds.get(combat_id, 0)
        return {"rounds": [{"round": number, "html": f"R{number}"} for number in range(known_upto + 1, total + 1)], "total_rounds_known": total}

    def fetch_combat_report_detail(self, _city_id, combat_id):
        self.summary_calls.append(combat_id)
        return {"html": f"<summary {combat_id}>", "date": "03.06.2026 12:54:33"}


def listed(combat_id, rounds, result="victory", city=55):
    return {"combat_id": combat_id, "rounds": rounds, "result": result, "city_id_target": city, "city_name": "Tebas", "owner_name": "Fulano"}


class CombatWatchTests(unittest.TestCase):
    WATCHING = {"started": True, "imported": True}

    def _watch(self, client, hub, state):
        return watch_combats(client, hub, game_account_id="g1", city_id=100, state=state)

    def test_the_first_look_brings_the_listed_combats_as_history_without_telling(self):
        client, hub = Client([listed(2, 1), listed(1, 3)], {1: 3, 2: 1}), Hub()
        result = self._watch(client, hub, None)
        self.assertEqual(client.detail_calls, [(2, 0), (1, 0)])
        self.assertEqual([(report["combat_id"], report.get("silent")) for _account, report in hub.saved], [(2, True), (1, True)])
        self.assertEqual(hub.saved[1][1]["detailed_html"].count("ROUND SEPARATOR"), 2)          # whole: rounds 1, 2 and 3
        self.assertEqual(result["state"], {"started": True, "imported": True, "seen": {
            "2": {"rounds": 1, "open": False, "sent": True}, "1": {"rounds": 3, "open": False, "sent": True}}})
        self.assertEqual((result["delivered"], result["imported"], result["open"]), (0, 2, 0))
        again = self._watch(client, hub, result["state"])                                        # once per account
        self.assertEqual((len(hub.saved), again["imported"]), (2, 0))

    def test_an_account_already_watched_gets_its_history_once_too(self):
        # the state of the accounts that were watched before the import existed
        state = {"started": True, "seen": {"432077": {"rounds": 1, "open": False}, "5": {"rounds": 2, "open": False, "sent": True}}}
        client, hub = Client([listed(432077, 1), listed(5, 2)], {432077: 1, 5: 2}), Hub()
        result = self._watch(client, hub, state)
        self.assertEqual([(report["combat_id"], report.get("silent")) for _account, report in hub.saved], [(432077, True)])   # 5 was already there
        self.assertTrue(result["state"]["imported"])
        self.assertEqual(result["state"]["seen"]["432077"], {"rounds": 1, "open": False, "sent": True})

    def test_the_history_is_a_few_of_the_newest_and_never_a_very_long_combat(self):
        rows = [listed(number, 1) for number in range(20, 13, -1)]                               # seven combats, the newest first
        rows[1] = listed(19, 22)                                                                 # 22 rounds: 22 requests for old news
        client, hub = Client(rows, {row["combat_id"]: row["rounds"] for row in rows}), Hub()
        result = self._watch(client, hub, None)
        self.assertEqual([report["combat_id"] for _account, report in hub.saved], [20, 18, 17, 16, 15])
        self.assertEqual(result["state"]["seen"]["19"], {"rounds": 22, "open": False})           # noted, to be told if it moves
        self.assertEqual(result["state"]["seen"]["14"], {"rounds": 1, "open": False})

    def test_a_combat_still_going_on_when_imported_keeps_being_followed_and_told(self):
        client, hub = Client([listed(9, 2, result="")], {9: 2}), Hub(finished=False)
        first = self._watch(client, hub, None)
        self.assertEqual((hub.saved[0][1].get("silent"), first["open"]), (True, 1))
        client.rounds[9], client.listed = 3, [listed(9, 3, result="")]
        moved = self._watch(client, hub, first["state"])
        self.assertEqual(client.detail_calls[-1], (9, 2))
        self.assertEqual((hub.saved[-1][1]["detailed_html"], hub.saved[-1][1].get("silent")), ("R3", None))   # the new round is told
        self.assertEqual(moved["delivered"], 1)

    def test_an_account_without_combats_has_its_first_combat_told_not_filed(self):
        client, hub = Client([], {}), Hub()
        first = self._watch(client, hub, None)
        self.assertEqual(first["state"], {"started": True, "imported": False, "seen": {}})     # one empty page may be a failure
        second = self._watch(client, hub, first["state"])
        self.assertTrue(second["state"]["imported"])                                            # two in a row: no combats
        client.listed, client.rounds = [listed(50, 1)], {50: 1}
        result = self._watch(client, hub, second["state"])
        self.assertEqual((hub.saved[0][1]["combat_id"], hub.saved[0][1].get("silent"), result["delivered"]), (50, None, 1))

    def test_the_accounts_watched_before_the_import_existed_with_an_empty_list_are_settled_at_once(self):
        result = self._watch(Client([], {}), Hub(), {"started": True, "imported": False, "seen": {}})
        self.assertTrue(result["state"]["imported"])

    def test_a_new_combat_is_delivered_with_its_rounds_and_summary(self):
        state = {**self.WATCHING, "seen": {"1": {"rounds": 3, "open": False}}}
        client, hub = Client([listed(5, 2), listed(1, 3)], {5: 2, 1: 3}), Hub(finished=True)
        result = self._watch(client, hub, state)
        self.assertEqual(client.detail_calls, [(5, 0)])                             # the known one is not opened again
        account, report = hub.saved[0]
        self.assertEqual((account, report["combat_id"], report["append_rounds"]), ("g1", 5, True))
        self.assertNotIn("silent", report)
        self.assertEqual(report["detailed_html"], "R1\n<!-- ROUND SEPARATOR -->\nR2")
        self.assertEqual((report["summary_html"], report["target_city_id"], report["target_owner"]), ("<summary 5>", "55", "Fulano"))
        self.assertEqual(result["state"]["seen"]["5"], {"rounds": 2, "open": False, "sent": True})
        self.assertEqual((result["delivered"], result["open"]), (1, 0))

    def test_a_combat_going_on_delivers_only_the_rounds_that_are_new(self):
        client, hub = Client([listed(7, 1, result="")], {7: 1}), Hub(finished=False)
        first = self._watch(client, hub, {**self.WATCHING, "seen": {}})
        self.assertEqual(first["state"]["seen"]["7"], {"rounds": 1, "open": True, "sent": True})
        self.assertEqual(first["open"], 1)

        client.rounds[7] = 1                                                        # nothing happened since
        idle = self._watch(client, hub, first["state"])
        self.assertEqual((len(hub.saved), idle["open"]), (1, 1))                    # looked, found nothing, still open
        self.assertEqual(client.detail_calls[-1], (7, 1))

        client.rounds[7] = 3
        client.listed = [listed(7, 3, result="victory")]
        hub.finished = True
        last = self._watch(client, hub, idle["state"])
        self.assertEqual(hub.saved[-1][1]["detailed_html"], "R2\n<!-- ROUND SEPARATOR -->\nR3")   # round 1 is not sent again
        self.assertEqual(hub.saved[-1][1]["result"], "victory")
        self.assertEqual((last["state"]["seen"]["7"], last["open"]), ({"rounds": 3, "open": False, "sent": True}, 0))

    def test_a_finished_combat_that_gains_rounds_in_the_list_is_opened_again(self):
        state = {**self.WATCHING, "seen": {"9": {"rounds": 2, "open": False, "sent": True}}}
        client, hub = Client([listed(9, 4)], {9: 4}), Hub()
        self._watch(client, hub, state)
        self.assertEqual(client.detail_calls, [(9, 2)])
        self.assertEqual(hub.saved[0][1]["detailed_html"], "R3\n<!-- ROUND SEPARATOR -->\nR4")

    def test_a_combat_that_was_only_noted_is_delivered_whole_when_it_moves(self):
        state = {**self.WATCHING, "seen": {"9": {"rounds": 22, "open": False}}}     # too long to be brought as history
        client, hub = Client([listed(9, 23, result="")], {9: 23}), Hub(finished=False)
        moved = self._watch(client, hub, state)
        self.assertEqual(client.detail_calls, [(9, 0)])
        self.assertEqual(hub.saved[0][1]["detailed_html"].count("ROUND SEPARATOR"), 22)   # the history is not cut
        self.assertEqual(moved["state"]["seen"]["9"], {"rounds": 23, "open": True, "sent": True})

    def test_what_the_hub_did_not_keep_is_delivered_again_next_time(self):
        state = {**self.WATCHING, "seen": {}}
        client = Client([listed(5, 1)], {5: 1})
        failed = self._watch(client, Hub(fail=True), state)
        self.assertNotIn("5", failed["state"]["seen"])
        hub = Hub()
        self._watch(client, hub, failed["state"])
        self.assertEqual(hub.saved[0][1]["combat_id"], 5)

    def test_combats_the_game_no_longer_lists_are_forgotten_but_an_empty_page_forgets_nothing(self):
        state = {**self.WATCHING, "seen": {"1": {"rounds": 1, "open": False}, "2": {"rounds": 1, "open": False}}}
        result = self._watch(Client([listed(2, 1)], {2: 1}), Hub(), state)
        self.assertEqual(list(result["state"]["seen"]), ["2"])
        result = self._watch(Client([], {}), Hub(), state)
        self.assertEqual(sorted(result["state"]["seen"]), ["1", "2"])


def _page(number: int, total: int) -> str:
    return (f'<div id="battlefield">battlefield Round<br/>{number} / {total} '
            f'<div id="slot11_21_0" class="slot army_small normal s303"><div class="number center"> 40 (-{number}) </div></div>'
            "<!-- " + "x" * 250 + " --></div>")


class DetailedReportTests(unittest.TestCase):
    def _action(self, total: int, zero_based: bool = True):
        """A game that answers round index+1 (or index, on a 1-based server); past the end it repeats the last."""
        client = MagicMock()
        asked = []

        def request(_method, _url, params=None, timeout=None):
            asked.append(params["combatRound"])
            number = params["combatRound"] + (1 if zero_based else 0)
            response = MagicMock()
            response.json.return_value = [["changeHTML", [_page(min(max(number, 1), total), total)]]]
            return response

        client._request.side_effect = request
        return FetchCombatDetailedReportAction(client=client), asked

    def test_everything_is_fetched_when_nothing_was_delivered(self):
        action, asked = self._action(3)
        result = action.execute(city_id=1, combat_id=9)
        self.assertEqual([item["round"] for item in result["rounds"]], [1, 2, 3])
        self.assertEqual((asked, result["attacker_losses"], result["total_rounds_known"]), ([0, 1, 2], {"303": 6}, 3))

    def test_only_the_rounds_after_the_delivered_ones_come_back(self):
        for zero_based in (True, False):
            action, asked = self._action(5, zero_based=zero_based)
            result = action.execute(city_id=1, combat_id=9, known_upto=3)
            self.assertEqual([item["round"] for item in result["rounds"]], [4, 5], zero_based)
            self.assertEqual(result["attacker_losses"], {"303": 9}, zero_based)     # the losses of rounds 4 and 5 only
            self.assertLessEqual(len(asked), 4, zero_based)

    def test_nothing_new_costs_one_request(self):
        action, asked = self._action(3)
        result = action.execute(city_id=1, combat_id=9, known_upto=3)
        self.assertEqual((result["rounds"], len(asked)), ([], 1))


class CombatListTests(unittest.TestCase):
    def test_a_combat_with_no_outcome_yet_is_listed_too(self):
        row = ('<tr class="{cls}"><td class="date"> 03.06. 12:54 </td><td class="right"> {rounds}</td>'
               '<td><a href="?view=militaryAdvisorReportView&combatId={cid}">x</a> '
               '<a href="?view=island&cityId=55">Tebas</a> <a href="?view=avatarProfile&avatarId=9">Fulano</a></td></tr>')
        html = "combatList <table>" + row.format(cls="green", rounds=2, cid=11) + row.format(cls="red alt", rounds=5, cid=12) \
            + row.format(cls="alt", rounds=1, cid=13) + "<tr><td>sem combate</td></tr></table>"
        client = MagicMock()
        client._request.return_value.json.return_value = [["changeView", ["militaryAdvisorCombatList", html]]]
        reports = FetchCombatReportsAction(client=client).execute(city_id=1)
        self.assertEqual([(item["combat_id"], item["result"], item["rounds"]) for item in reports], [(11, "victory", 2), (12, "defeat", 5), (13, "", 1)])
        self.assertEqual((reports[0]["city_id_target"], reports[0]["owner_name"]), (55, "Fulano"))


if __name__ == "__main__":
    unittest.main()
