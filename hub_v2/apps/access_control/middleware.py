from django.http import Http404, HttpResponseForbidden
from .catalog import OWNER, required_permission
from .policy import allowed, permissions


class CapabilityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and not user.is_superuser:
            permission = required_permission(request.path, request.method)
            if permission and not request.path.startswith(("/organizacao/selecionar/", "/settings/tour/done/", "/settings/tour/state/")):
                if not allowed(user, permission, request.organization):
                    if permission.startswith("platform.") or permission in ("organization.organization.manage", "organization.billing.manage"):
                        raise Http404()
                    return HttpResponseForbidden("Você não tem permissão para esta operação nesta organização.")
                # Legacy member/invitation forms can grant the complete owner role.
                # Partial delegates use the new role editor, whose ceiling is checked per grant.
                if request.path.startswith("/organizacao/membros/") and request.method == "POST" and not OWNER <= permissions(user, request.organization):
                    return HttpResponseForbidden("Use Papéis e acessos para delegar permissões específicas.")
        return self.get_response(request)
