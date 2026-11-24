"""A static description of registered agent routes; never inspect production objects."""
import inspect
import re
from collections import defaultdict
from django.urls import URLResolver, get_resolver


def agent_routes():
    groups = defaultdict(list)
    def walk(patterns, prefix=""):
        for item in patterns:
            route = prefix + str(item.pattern)
            if isinstance(item, URLResolver):
                walk(item.url_patterns, route)
            elif route.startswith("api/agent/"):
                cls = getattr(item.callback, "cls", None)
                if cls is None:
                    continue
                path = "/" + re.sub(r"<(?:(?:\w+):)?(\w+)>", r"{\1}", route)
                text = inspect.getdoc(cls) or cls.__name__
                for method in cls.http_method_names:
                    if method not in ("head", "options") and callable(getattr(cls, method, None)):
                        groups[route.split("/")[2]].append({"method": method.upper(), "path": path,
                            "description": text, "authentication": "Token do host" if "/supervisor/" in path else "Token individual do nó"})
    walk(get_resolver().url_patterns)
    return [{"name": name, "routes": sorted(rows, key=lambda row: (row["path"],row["method"]))} for name,rows in sorted(groups.items())]
