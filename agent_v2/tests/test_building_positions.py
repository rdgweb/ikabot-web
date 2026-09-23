"""N-50: reorganizar edificios -- port of the game's swapPositionsIfPossible
(rules) and logSuccessfulMoves (moves list), plus the POST body format."""

import unittest

from game_client.actions.building_positions import moves_payload, plan_building_moves

LAND = [4, 6, 7, 9, 11, 12, 13, 15, 16, 17, 23]
SHORE = [3, 5]


def _pos(building_id, allowed, **extra):
    return {"buildingId": building_id, "allowedBuildings": allowed, "isBusy": False, **extra}


def _city():
    return [
        _pos(0, [0]),              # 0 townHall
        _pos(3, SHORE),            # 1 port
        _pos(None, SHORE),         # 2 empty shore
        _pos(11, LAND),            # 3 palace
        _pos(7, LAND),             # 4 warehouse
        _pos(9, LAND),             # 5 tavern
        _pos(None, LAND),          # 6 empty land
        _pos(8, [8]),              # 7 wall
    ]


class PlanBuildingMovesTests(unittest.TestCase):
    def test_swap_of_two_buildings_produces_both_moves(self):
        plan = plan_building_moves(_city(), [[3, 4]])

        self.assertEqual(plan["moves"], [{"from": 3, "to": 4}, {"from": 4, "to": 3}])
        self.assertEqual(plan["applied"], [[3, 4]])
        self.assertEqual([p["buildingId"] for p in plan["positions"]][3:5], [7, 11])

    def test_moving_onto_an_empty_slot_is_a_single_move(self):
        plan = plan_building_moves(_city(), [[5, 6]])

        self.assertEqual(plan["moves"], [{"from": 5, "to": 6}])

    def test_chained_swaps_rewrite_earlier_moves_like_the_game(self):
        # palace 3->4 (swap with warehouse), then palace 4->6 (empty)
        plan = plan_building_moves(_city(), [[3, 4], [4, 6]])

        self.assertEqual(plan["moves"], [{"from": 3, "to": 6}, {"from": 4, "to": 3}])
        ids = [p["buildingId"] for p in plan["positions"]]
        self.assertEqual((ids[3], ids[4], ids[6]), (7, None, 11))

    def test_ground_type_rules_are_enforced(self):
        plan = plan_building_moves(_city(), [[1, 4], [7, 6], [0, 3]])

        self.assertEqual(plan["moves"], [])
        self.assertEqual([r["reason"] for r in plan["rejected"]],
                         ["destino_nao_aceita", "destino_nao_aceita", "destino_nao_aceita"])

    def test_empty_origin_busy_and_queued_positions_are_rejected(self):
        city = _city()
        city[4]["isBusy"] = True
        city[5]["inConstructionList"] = True
        plan = plan_building_moves(city, [[6, 3], [3, 4], [3, 5], [3, 3], [3, 99], ["x"]])

        self.assertEqual([r["reason"] for r in plan["rejected"]], [
            "origem_vazia", "ocupado", "na_fila_de_construcao", "posicao_invalida", "posicao_invalida", "formato_invalido",
        ])
        self.assertEqual(plan["moves"], [])

    def test_townhall_building_id_zero_is_not_treated_as_empty(self):
        city = [_pos(0, [0, 7]), _pos(7, [0, 7])]

        self.assertEqual(plan_building_moves(city, [[0, 1]])["moves"], [{"from": 0, "to": 1}, {"from": 1, "to": 0}])

    def test_input_positions_are_not_mutated(self):
        city = _city()
        plan_building_moves(city, [[3, 4]])

        self.assertEqual(city[3]["buildingId"], 11)

    def test_moves_payload_matches_jquery_serialisation(self):
        self.assertEqual(
            moves_payload(39274, [{"from": 3, "to": 7}, {"from": 7, "to": 3}]),
            {"moves[0][from]": "3", "moves[0][to]": "7", "moves[1][from]": "7", "moves[1][to]": "3", "cityId": "39274"},
        )


if __name__ == "__main__":
    unittest.main()
