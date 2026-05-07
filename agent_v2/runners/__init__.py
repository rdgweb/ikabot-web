"""
Runners package — one module per domain.

Importing this package triggers all @register_runner decorators,
populating the global registry in core.runner_registry.
"""

from runners import (  # noqa: F401
    base,
    auth,
    resources,
    military,
    market,
    city,
    economy,
    research,
    academy,
    piracy,
    misc,
    monitoring,
    diplomacy,
    check_status,
    discover_characters,
    donate,
    donate_loop,
    revolt,
    spy,
)
