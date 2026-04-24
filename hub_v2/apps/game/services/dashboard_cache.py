from django.core.cache import cache

_DASHBOARD_VERSION_CACHE_KEY = "game_dashboard_version"


def get_dashboard_cache_key(user_id) -> str:
    version = cache.get(_DASHBOARD_VERSION_CACHE_KEY) or "0"
    return f"game_dashboard_{user_id}_v{version}"


def bump_dashboard_cache_version() -> str:
    cache.add(_DASHBOARD_VERSION_CACHE_KEY, 0, None)
    version = cache.incr(_DASHBOARD_VERSION_CACHE_KEY)
    return str(version)
