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
    """Dedicated locally hosted Scalar reference for authorized staff."""

    template_name = "docs/api_reference.html"

    def dispatch(self, request, *args, **kwargs):
        from core.access import platform_admin
        from django.http import Http404
        if not platform_admin(request.user):
            raise Http404()
        if request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=204)
            response["HX-Redirect"] = resolve_url("api-docs")
            return response
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
        from .openapi import agent_schema
        return Response(agent_schema())
