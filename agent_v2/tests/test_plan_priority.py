"""N-108: a step prioritized in the hub panel is what the city builds, until it reaches the chosen level."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Other test modules leave bare stub packages behind; this one needs the real ones.
for _name in [n for n in sys.modules if n.split(".")[0] in ("game_client", "runners", "core", "services", "sessions")]:
    if not getattr(sys.modules[_name], "__file__", None):
        del sys.modules[_name]

from runners.city import ConstructionPlanRunner  # noqa: E402


def _city(barracks=4, warehouse=10, academy=5):
    return {
        "id": "1", "name": "Atenas", "wood": 10_000_000, "wine": 0, "marble": 0, "crystal": 0, "sulfur": 0,
        "resource_production_per_hour": 0, "tradegood_production_per_hour": 0,
        "buildings": [
            {"building": "barracks", "level": barracks, "position": 3},
            {"building": "warehouse", "level": warehouse, "position": 4},
            {"building": "academy", "level": academy, "position": 5},
        ],
    }


def _step(index, building, target, *, seconds=100, **marks):
    """A step as the hub stores it in construction_plan_steps (with the cost and time of each level)."""
    return {
        "index": index, "city_id": "1", "city_name": "Atenas", "building_id": building, "building_name": building,
        "mode": "upgrade", "target_level": target,
        "level_rows": [
            {"level": level, "adjusted_seconds": seconds, "base_seconds": seconds,
             "costs": {"wood": 10, "wine": 0, "marble": 0, "glas": 0, "sulfur": 0}}
            for level in range(1, target + 1)
        ],
        **marks,
    }


def _pick(city, raw_steps, strategy="eta_first"):
    plan = ConstructionPlanRunner._normalized_plan({"construction_plan_steps": raw_steps})
    return ConstructionPlanRunner._pick_pending_step_for_city([city], plan, "1", strategy, [])


class PlanPriorityTests(unittest.TestCase):
    def test_the_mark_of_the_panel_reaches_the_plan_the_runner_uses(self):
        plan = ConstructionPlanRunner._normalized_plan({"construction_plan_steps": [
            _step(1, "barracks", 35, priority_order=2, priority_level=30),
            _step(2, "warehouse", 20),
            _step(3, "academy", 9, priority=True),                   # the old yes/no mark
        ]})
        self.assertEqual([(s["building_id"], s["priority_order"], s["priority_level"]) for s in plan],
                         [("barracks", 2, 30), ("warehouse", 0, 0), ("academy", 1, 9)])

    def test_a_prioritized_step_is_the_only_one_built_until_its_level(self):
        # the real request: "se priorizar o nível 30 do quartel, e o quartel estiver nível 4, até chegar ao 30 só ele é feito"
        steps = [
            _step(1, "warehouse", 20, seconds=10),                   # quicker: what "menor ETA" would pick
            _step(2, "barracks", 35, seconds=5000, priority_order=1, priority_level=30),
        ]
        for strategy in ("eta_first", "smart", "fifo"):
            self.assertEqual(_pick(_city(barracks=4), steps, strategy)["building_id"], "barracks", strategy)
            self.assertEqual(_pick(_city(barracks=29), steps, strategy)["building_id"], "barracks", strategy)

    def test_once_the_level_is_reached_the_plan_goes_back_to_its_own_order(self):
        steps = [
            _step(1, "warehouse", 20, seconds=10),
            _step(2, "barracks", 35, seconds=5000, priority_order=1, priority_level=30),
        ]
        # at 30 the barracks still has 31-35 in the plan, but it is no longer ahead of the others
        self.assertEqual(_pick(_city(barracks=30), steps, "eta_first")["building_id"], "warehouse")
        self.assertEqual(_pick(_city(barracks=30, warehouse=20), steps, "eta_first")["building_id"], "barracks")

    def test_priorities_go_in_their_order(self):
        steps = [
            _step(1, "academy", 12, priority_order=2, priority_level=10),
            _step(2, "warehouse", 20, seconds=10),
            _step(3, "barracks", 35, priority_order=1, priority_level=6),
        ]
        self.assertEqual(_pick(_city(barracks=4, academy=5), steps)["building_id"], "barracks")     # 1 first
        self.assertEqual(_pick(_city(barracks=6, academy=5), steps)["building_id"], "academy")      # then 2
        self.assertEqual(_pick(_city(barracks=6, academy=10), steps)["building_id"], "warehouse")   # then the strategy

    def test_a_priority_without_a_level_goes_to_the_target_of_the_step(self):
        steps = [_step(1, "warehouse", 20, seconds=10), _step(2, "barracks", 8, seconds=5000, priority=True)]
        self.assertEqual(_pick(_city(barracks=7), steps)["building_id"], "barracks")
        self.assertEqual(_pick(_city(barracks=8), steps)["building_id"], "warehouse")

    def test_without_marks_nothing_changes(self):
        steps = [_step(1, "barracks", 35, seconds=5000), _step(2, "warehouse", 20, seconds=10)]
        self.assertEqual(_pick(_city(), steps, "eta_first")["building_id"], "warehouse")
        self.assertEqual(_pick(_city(), steps, "fifo")["building_id"], "barracks")

    def test_a_step_cut_in_the_panel_stops_at_its_new_target(self):
        # the hub cuts "30 to 35" by lowering the target to 29 in the list the runner reads
        steps = [_step(1, "barracks", 29)]
        self.assertEqual(_pick(_city(barracks=28), steps)["building_id"], "barracks")
        self.assertIsNone(_pick(_city(barracks=29), steps))


if __name__ == "__main__":
    unittest.main()
