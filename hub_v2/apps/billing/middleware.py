"""Read-only organizations (N-124): 30 days past due, a customer can look but not change anything."""
from django.shortcuts import render

ALWAYS = ("/organizacao/", "/logout/", "/login/", "/api/", "/settings/tour/", "/stats/", "/conta/")
VIEWER_ALWAYS = ("/logout/", "/login/", "/api/", "/settings/tour/", "/stats/", "/conta/", "/organizacao/selecionar/")


class ReadOnlyOrganizationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        manual_operation = request.path.startswith("/game/control/") and not request.path.endswith("/end/")
        if manual_operation or request.method in ("POST", "PUT", "PATCH", "DELETE"):
            user = getattr(request, "user", None)
            organization = getattr(request, "organization", None)
            if user is not None and user.is_authenticated:
                from apps.orgs.models import Membership
                from apps.orgs.services import role_of

                # N-126: a viewer only looks — only their own account and leaving are theirs
                from core.access import operator, platform_admin
                platform_path = request.path.startswith(("/settings/", "/notes/", "/users/", "/sistema/", "/proxy/", "/captcha/", "/telegram/", "/telemetry/", "/django-admin/", "/accounts/docker-hosts/"))
                if not request.path.startswith(VIEWER_ALWAYS) and not platform_path and not operator(user, organization):
                    return render(request, "billing/viewer_only.html", status=403)
                if not request.path.startswith(ALWAYS) and not (platform_path and platform_admin(user)):
                    from .services import state_for

                    if organization is None or not state_for(organization).allows_work:
                        return render(request, "billing/read_only.html", status=402)
        return self.get_response(request)
