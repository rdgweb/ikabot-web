"""Net gold income of an account (N-76), from the snapshot's base data.

The same sum the game shows in the gold tooltip of the page header:

    income + bad_tax_accountant + god_gold_result + scientists_upkeep + upkeep

Each value keeps the sign the game sends (the two upkeeps arrive negative; the
premium "Recibos de Ouro Aumentados" and the shrine "Deuses (Pluto)" bonuses arrive
positive). A snapshot from before the bonuses were collected has no such keys and
counts them as zero.

An estimate of gold per hour only: the balance stays the authority for spending.
"""

from __future__ import annotations

from typing import Any

# snapshot key -> key in the game's headerData
GOLD_INCOME_FIELDS = {
    "income": "income",
    "bad_tax_accountant": "badTaxAccountant",
    "god_gold_result": "godGoldResult",
    "scientists_upkeep": "scientistsUpkeep",
    "upkeep": "upkeep",
}


def _number(value: Any) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def gold_income_parts(base_snapshot: dict[str, Any] | None) -> dict[str, int]:
    base = base_snapshot if isinstance(base_snapshot, dict) else {}
    return {key: _number(base.get(key)) for key in GOLD_INCOME_FIELDS}


def net_gold_income(base_snapshot: dict[str, Any] | None) -> int:
    """Gold per hour the balance really changes by (can be zero or negative)."""
    return sum(gold_income_parts(base_snapshot).values())
