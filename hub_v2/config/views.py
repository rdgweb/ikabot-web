"""Custom error views and auth helpers."""

from django.conf import settings
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LogoutView
from django.http import HttpResponse
from django.shortcuts import render
from django.shortcuts import resolve_url
from django.views.generic import TemplateView
from drf_spectacular.views import SpectacularAPIView
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


class ApiDocsView(LoginRequiredMixin, TemplateView):
    """Serve Scalar-based API documentation for authenticated users."""

    template_name = "docs/api_reference.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "schema_url": resolve_url("api-schema"),
                "api_title": "ikabot hub Agent API",
                "api_version": settings.VERSION,
            }
        )
        return context


class AuthenticatedSchemaView(SpectacularAPIView):
    """Expose the OpenAPI schema only to logged-in hub users."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
