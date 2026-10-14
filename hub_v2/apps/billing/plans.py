"""The price table (N-124), in one place: monthly, in reais, by ACTIVE sub-accounts."""
from decimal import Decimal

# (most active sub-accounts, price per month)
TIERS = [
    (4, Decimal("14.90")),
    (10, Decimal("29.90")),
    (20, Decimal("49.90")),
    (40, Decimal("89.90")),
    (80, Decimal("149.90")),
]
ABOVE_EACH = Decimal("1.90")          # above 80: per sub-account, chosen in steps of 10
ABOVE_STEP = 10
ABOVE_MAX = 300

TRIAL_DAYS = 7
TRIAL_LIMIT = 4
GRACE_DAYS = 3                       # after the due date, work goes on this long
READ_ONLY_DAYS = 30                  # after the due date, the organization only reads


def limits() -> list[int]:
    top = TIERS[-1][0]
    return [limit for limit, _price in TIERS] + list(range(top + ABOVE_STEP, ABOVE_MAX + 1, ABOVE_STEP))


def price_for(limit: int) -> Decimal:
    for most, price in TIERS:
        if limit <= most:
            return price
    return (ABOVE_EACH * limit).quantize(Decimal("0.01"))


def tier_for(active: int) -> int:
    """The smallest choice that fits this many active sub-accounts."""
    for limit in limits():
        if active <= limit:
            return limit
    return limits()[-1]


def brl(value) -> str:
    return "R$ " + f"{Decimal(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
