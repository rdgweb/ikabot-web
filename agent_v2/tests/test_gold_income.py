"""N-76: the net gold income is the whole sum of the game's gold tooltip, bonuses included."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Other test modules leave bare stub packages behind; this one needs the real ones.
for _name in [n for n in sys.modules if n.split(".")[0] in ("game_client", "runners", "core", "services")]:
    if not getattr(sys.modules[_name], "__file__", None):
        del sys.modules[_name]

from runners.check_status import CheckStatusRunner  # noqa: E402
from runners.city import ConstructionPlanRunner  # noqa: E402
from services.gold_income import gold_income_parts, net_gold_income  # noqa: E402

# headerData as the game sends it (figures of a real account on 07/10/2026; nothing
# identifying). The game's own tooltip for it reads:
#   Rendimento 5.037 | Recibos de Ouro Aumentados 0 | Deuses (Pluto) 0 | Cientista -1.704 | Manutencao -209 | Total 3.124
REAL_HEADER = {
    "gold": "2467246.8665555785", "income": 5037.0953210297793703, "upkeep": -209, "scientistsUpkeep": -1704,
    "godGoldResult": 0, "badTaxAccountant": 0,
    "freeTransporters": 63, "maxTransporters": 63, "freeFreighters": 0, "maxFreighters": 0,
    "resourceProduction": 0.4267083333333333, "tradegoodProduction": 0.28583333333333333, "wineSpendings": 181,
    "currentResources": {"citizens": 184.15, "population": 1850.15, "resource": 556623, "1": 224912, "2": 146034, "3": 252043, "4": 413601},
}


def _snapshot_base(header):
    """What check_status stores for this headerData (the gold part)."""
    session = MagicMock()
    session.get.return_value.text = json.dumps([["updateGlobalData", {"headerData": header}]])
    data = CheckStatusRunner._fetch_global_data(object.__new__(CheckStatusRunner), session, "https://s1-br.example/index.php?")
    return {
        "gold": data.get("gold", 0), "income": data.get("income", 0), "upkeep": data.get("upkeep", 0),
        "scientists_upkeep": data.get("scientistsUpkeep", 0),
        "god_gold_result": data.get("godGoldResult", 0), "bad_tax_accountant": data.get("badTaxAccountant", 0),
    }


class GoldIncomeTests(unittest.TestCase):
    def test_real_account_matches_the_total_the_game_shows(self):
        base = _snapshot_base(REAL_HEADER)

        self.assertEqual(gold_income_parts(base), {
            "income": 5037, "bad_tax_accountant": 0, "god_gold_result": 0, "scientists_upkeep": -1704, "upkeep": -209,
        })
        self.assertEqual(net_gold_income(base), 3124)

    def test_both_bonuses_are_collected_and_added_once(self):
        base = _snapshot_base({**REAL_HEADER, "godGoldResult": 1500.4, "badTaxAccountant": "1007"})

        self.assertEqual((base["god_gold_result"], base["bad_tax_accountant"]), (1500, 1007))
        self.assertEqual(net_gold_income(base), 5037 + 1007 + 1500 - 1704 - 209)
        # the income itself is stored as the game sent it: the bonuses are not folded into it
        self.assertEqual(int(base["income"]), 5037)

    def test_an_adjustment_can_be_negative(self):
        self.assertEqual(net_gold_income({"income": 1000, "upkeep": -200, "scientists_upkeep": -100, "bad_tax_accountant": -300}), 400)
        self.assertEqual(net_gold_income({"income": 100, "upkeep": -500, "god_gold_result": 50}), -350)

    def test_snapshots_from_before_the_bonuses_keep_their_old_value(self):
        old = {"gold": 1000, "income": 16021, "upkeep": -20353, "scientists_upkeep": -1929}
        self.assertEqual(net_gold_income(old), 16021 - 20353 - 1929)
        self.assertEqual(net_gold_income({}), 0)
        self.assertEqual(net_gold_income(None), 0)
        self.assertEqual(net_gold_income({"income": None, "upkeep": "", "god_gold_result": "abc", "bad_tax_accountant": []}), 0)

    def test_game_without_the_fields_stores_zero(self):
        header = {key: value for key, value in REAL_HEADER.items() if key not in ("godGoldResult", "badTaxAccountant")}
        base = _snapshot_base(header)
        self.assertEqual((base["god_gold_result"], base["bad_tax_accountant"]), (0, 0))
        self.assertEqual(net_gold_income(base), 3124)


class GoldWaitTests(unittest.TestCase):
    """How long the construction plan waits for gold before buying on the market."""

    @staticmethod
    def _wait(base, available=40_000, minimum=100_000):
        return ConstructionPlanRunner._estimate_market_gold_wait_seconds(
            base_snapshot=base, detail={"available_gold": available, "min_gold": minimum},
        )

    def test_known_deficit(self):
        # 60.000 short at 3.000/h: 20 hours
        base = {"income": 5000, "upkeep": -500, "scientists_upkeep": -1500}
        self.assertEqual(self._wait(base), 20 * 3600)

    def test_a_bonus_shortens_the_wait(self):
        base = {"income": 5000, "upkeep": -500, "scientists_upkeep": -1500, "god_gold_result": 2000, "bad_tax_accountant": 1000}
        self.assertEqual(self._wait(base), 10 * 3600)          # 60.000 at 6.000/h

    def test_a_bonus_can_be_what_makes_the_income_positive(self):
        negative = {"income": 1000, "upkeep": -1500, "scientists_upkeep": -200}
        self.assertIsNone(self._wait(negative))               # never gets there: no estimate
        self.assertEqual(self._wait({**negative, "god_gold_result": 1300}), 100 * 3600)        # 60.000 at 600/h

    def test_zero_or_negative_income_has_no_estimate_and_no_deficit_has_no_wait(self):
        self.assertIsNone(self._wait({"income": 500, "upkeep": -500}))
        self.assertIsNone(self._wait({"income": 5000, "upkeep": -500, "bad_tax_accountant": -6000}))
        self.assertEqual(self._wait({"income": 0}, available=150_000), 0)


if __name__ == "__main__":
    unittest.main()
