"""Custom error views and auth helpers."""

from django.conf import settings
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LogoutView
from django.http import HttpResponse
from django.shortcuts import render
from django.shortcuts import resolve_url
from django.views.generic import TemplateView
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.renderers import JSONRenderer
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated


def custom_404(request, exception):
    return render(request, "errors/404.html", status=404)


def custom_500(request):
    return render(request, "errors/500.html", status=500)


class HtmxLogoutView(LogoutView):
    """Use HX-Redirect for boosted navigation so the full layout resets."""

    def post(self, request, *args, **kwargs):
        is_htmx = request.headers.get("HX-Request") == "true"
        if is_htmx:
            auth_logout(request)
            response = HttpResponse(status=204)
            response["HX-Redirect"] = self.get_success_url()
            return response
        return super().post(request, *args, **kwargs)

    def get_success_url(self):
        return resolve_url(self.next_page or settings.LOGOUT_REDIRECT_URL)


class ApiDocsView(TemplateView):
    """Native route reference for platform administrators."""

    template_name = "docs/api_reference.html"

    def dispatch(self, request, *args, **kwargs):
        from core.access import platform_admin
        from django.http import Http404
        if not platform_admin(request.user):
            raise Http404()
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "schema_url": resolve_url("api-schema"),
                "api_title": "ikabot hub Agent API",
                "api_version": settings.VERSION,
            }
        )
        from .api_catalog import agent_routes
        context["api_groups"] = agent_routes()
        return context


class AuthenticatedSchemaView(APIView):
    """Expose the OpenAPI schema only to platform administrators."""

    authentication_classes = [SessionAuthentication]
    renderer_classes = [JSONRenderer]
    from core.auth.permissions import IsAdminUser
    permission_classes = [IsAdminUser]

    def get(self, request):
        from .api_catalog import agent_routes
        import re
        paths = {}
        for group in agent_routes():
            for route in group["routes"]:
                host = route["authentication"] == "Token do host"
                paths.setdefault(route["path"], {})[route["method"].lower()] = {
                    "description": route["description"], "tags": [group["name"]],
                    "security": [{"HostToken" if host else "AgentToken": []}],
                    "parameters": [{"name": key,"in":"path","required":True,"schema":{"type":"integer"} if key == "pk" else {"type":"string","format":"uuid"}}
                                   for key in re.findall(r"\{(\w+)\}",route["path"])] + [
                        {"name":"X-Ikabot-Host-ID" if host else "X-Agent-Node-ID","in":"header","required":True,"schema":{"type":"string","format":"uuid"}}],
                    "responses": {str(code):{"description":label} for code,label in (
                        (200,"Operação recebida; formato depende da rota"),(400,"Dados inválidos"),
                        (401,"Credencial inválida"),(403,"Operação sem autorização"),(404,"Recurso indisponível para esta credencial"))},
                }
        return Response({"openapi":"3.0.3","info":{"title":"API interna de agents", "version":settings.VERSION,
            "description":"Catálogo das rotas registradas. Não define os corpos completos de entrada/saída; a validação é específica de cada endpoint."},
            "paths": paths, "components":{"securitySchemes":{
                "AgentToken":{"type":"apiKey","in":"header","name":"X-Agent-Token"},
                "HostToken":{"type":"apiKey","in":"header","name":"X-Ikabot-Host-Token"}}}})
