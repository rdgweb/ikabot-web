"""Read-only monthly estimates using the same price table as billing."""
from dataclasses import dataclass
from decimal import Decimal

from . import plans


@dataclass(frozen=True)
class MonthlyEstimate:
    limit: int
    base: Decimal
    agents: Decimal
    proxies: Decimal

    @property
    def total(self):
        return self.base + self.agents + self.proxies


def estimate_monthly(*, active_subaccounts, platform_agents=0, platform_proxies=0, limit=None):
    counts = (active_subaccounts, platform_agents, platform_proxies)
    if any(type(value) is not int or value < 0 for value in counts):
        raise ValueError('Invalid account count')
    limit = plans.tier_for(active_subaccounts) if limit is None else limit
    if limit not in plans.limits():
        raise ValueError('Invalid plan tier')
    return MonthlyEstimate(limit, plans.price_for(limit), plans.ADDON_AGENT * platform_agents,
                           plans.ADDON_PROXIES * platform_proxies)
