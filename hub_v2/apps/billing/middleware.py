"""Read-only organizations (N-124): 30 days past due, a customer can look but not change anything."""
from django.shortcuts import render

ALWAYS = ("/organizacao/", "/logout/", "/login/", "/api/", "/settings/tour/", "/stats/", "/conta/")
VIEWER_ALWAYS = ("/logout/", "/login/", "/api/", "/settings/tour/", "/stats/", "/conta/")


class ReadOnlyOrganizationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            user = getattr(request, "user", None)
            organization = getattr(request, "organization", None)
            if user is not None and user.is_authenticated and organization is not None and not (user.is_staff or user.is_superuser):
                from apps.orgs.models import Membership
                from apps.orgs.services import role_of

                # N-126: a viewer only looks — only their own account and leaving are theirs
                if not request.path.startswith(VIEWER_ALWAYS) and role_of(user, organization) == Membership.VIEWER:
                    return render(request, "billing/viewer_only.html", status=403)
                if not request.path.startswith(ALWAYS):
                    from .services import state_for

                    if state_for(organization).read_only:
                        return render(request, "billing/read_only.html", status=402)
        return self.get_response(request)
