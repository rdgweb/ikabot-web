"""Registry mapping action_code to runner classes."""

import logging

logger = logging.getLogger(__name__)

_REGISTRY: dict[int, type] = {}


def register_runner(action_code: int):
    """Decorator to register a runner for an action code."""
    def decorator(cls):
        _REGISTRY[action_code] = cls
        return cls
    return decorator


def get_runner(action_code: int):
    """Get runner class for action code. Falls back to GenericRunner."""
    from runners.base import GenericRunner
    runner = _REGISTRY.get(action_code, GenericRunner)
    if runner.__name__ == "GenericRunner" and action_code not in _REGISTRY:
        logger.warning(f"No runner registered for action_code={action_code}, using GenericRunner")
    return runner
