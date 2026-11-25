"""Keep annotated request/response contracts and disclose unannotated operations."""
from functools import lru_cache
import re
from django.urls import get_resolver
from drf_spectacular.generators import SchemaGenerator
from .api_catalog import agent_routes


@lru_cache(maxsize=1)
def agent_schema():
    patterns = [p for p in get_resolver().url_patterns if str(p.pattern) == "api/agent/"]
    schema = SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)
    paths = schema.setdefault("paths", {})
    schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schemes.update({"AgentToken": {"type": "apiKey", "in": "header", "name": "X-Agent-Token"},
                    "HostToken": {"type": "apiKey", "in": "header", "name": "X-Ikabot-Host-Token"}})
    for group in agent_routes():
        for route in group["routes"]:
            path, method = route["path"], route["method"].lower()
            # Spectacular normalizes Django's pk converter to id.
            if path not in paths and "{pk}" in path and path.replace("{pk}", "{id}") in paths:
                path = path.replace("{pk}", "{id}")
            operation = paths.setdefault(path, {}).setdefault(method, {
                "operationId": re.sub(r"\W+", "_", path).strip("_") + "_" + method,
                "description": route["description"], "tags": [group["name"]],
                "responses": {"200": {"description": "Resposta específica desta operação."}},
                "parameters": [{"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
                               for name in re.findall(r"\{(\w+)\}", path)]})
            host = route["authentication"] == "Token do host"
            operation["security"] = [{"HostToken" if host else "AgentToken": []}]
            header = "X-Ikabot-Host-ID" if host else "X-Agent-Node-ID"
            params = operation.setdefault("parameters", [])
            if not any(p.get("name") == header for p in params):
                params.append({"name": header, "in": "header", "required": True, "schema": {"type": "string", "format": "uuid"}})
            content = [r.get("content") for r in operation.get("responses", {}).values() if isinstance(r, dict)]
            partial = (method in ("post", "put", "patch") and "requestBody" not in operation) or not any(content)
            if partial:
                operation["x-contract-partial"] = True
                operation["description"] = operation.get("description", "") + "\n\nContrato parcial: entrada ou saída ainda não possui esquema tipado completo. Consulte a implementação antes de integrar."
    return schema
