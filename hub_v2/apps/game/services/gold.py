"""Net gold income of an account (N-76): one rule for the dashboard, the history,
the Generals' Bank and anything else that estimates gold per hour.

It is the sum the game itself shows in the gold tooltip of the page header:

    Rendimento                    income               (cities)
    Recibos de Ouro Aumentados    bad_tax_accountant   (premium bonus)
    Deuses (Pluto)                god_gold_result      (shrine bonus)
    Cientista                     scientists_upkeep    (already negative)
    Manutencao                    upkeep               (already negative)
    Total

Every value keeps the sign the game sends. The two bonuses reach the snapshot from
agent 0.1.90 on; a snapshot without them (older agent, older history row) simply has
no bonus, which is what the calculation was before.

This is an estimate of gold per hour. The balance itself (`gold`) stays the authority
for anything that spends.
"""

from __future__ import annotations


def _number(value) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def gold_income(base: dict | None) -> dict:
    """{"income", "accountant_bonus", "god_bonus", "receipts", "upkeep", "scientists_upkeep", "net"} per hour.

    `receipts` is everything that comes in (income plus the two bonuses); `net` is
    what the balance really changes by.
    """
    base = base if isinstance(base, dict) else {}
    income = _number(base.get("income"))
    accountant_bonus = _number(base.get("bad_tax_accountant"))
    god_bonus = _number(base.get("god_gold_result"))
    upkeep = _number(base.get("upkeep"))
    scientists_upkeep = _number(base.get("scientists_upkeep"))
    receipts = income + accountant_bonus + god_bonus
    return {
        "income": income,
        "accountant_bonus": accountant_bonus,
        "god_bonus": god_bonus,
        "receipts": receipts,
        "upkeep": upkeep,
        "scientists_upkeep": scientists_upkeep,
        "net": receipts + upkeep + scientists_upkeep,
    }


def net_gold_income(base: dict | None) -> int:
    return gold_income(base)["net"]
