"""drf-spectacular extensions for custom project components."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class AgentTokenAuthenticationScheme(OpenApiAuthenticationExtension):
    """Describe the custom X-Agent-Token auth header in OpenAPI."""

    target_class = "core.auth.backends.AgentTokenAuthentication"
    name = "AgentToken"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "X-Agent-Token",
            "description": "Token individual do nó. Envie também X-Agent-Node-ID; a credencial não concede acesso a outros nós ou organizações.",
        }
