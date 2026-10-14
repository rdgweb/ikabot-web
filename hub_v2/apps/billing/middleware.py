"""Read-only organizations (N-124): 30 days past due, a customer can look but not change anything."""
from django.shortcuts import render

ALWAYS = ("/organizacao/", "/logout/", "/login/", "/api/", "/settings/tour/", "/stats/")


class ReadOnlyOrganizationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and not request.path.startswith(ALWAYS):
            user = getattr(request, "user", None)
            organization = getattr(request, "organization", None)
            if user is not None and user.is_authenticated and organization is not None and not (user.is_staff or user.is_superuser):
                from .services import state_for

                if state_for(organization).read_only:
                    return render(request, "billing/read_only.html", status=402)
        return self.get_response(request)
