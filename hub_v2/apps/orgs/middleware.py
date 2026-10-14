from . import scope


class OrganizationScopeMiddleware:
    """A logged person's request only reaches their organization's data (N-106, stage 3a)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            request.organization = None
            return self.get_response(request)
        organization = scope.organization_for(user)
        request.organization = organization
        token = scope.activate(organization, deny=organization is None)
        try:
            return self.get_response(request)
        finally:
            scope.deactivate(token)


# N-106: pages of the platform itself (internal notes, global settings and integrations, users,
# platform proxies). A customer never reaches them; for them the page does not exist.
PLATFORM_ONLY = (
    "/django-admin/", "/api/schema/", "/api/docs/", "/notes/", "/settings/", "/users/", "/captcha/",
    "/telegram/", "/proxy/", "/telemetry/", "/accounts/docker-hosts/",
)
OPEN_TO_EVERYONE = ("/settings/tour/done/", "/settings/tour/state/")   # the switch (tour/toggle/) is the platform's


def is_platform_staff(user) -> bool:
    return bool(user.is_superuser or user.is_staff)


class PlatformPagesMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if path.startswith(PLATFORM_ONLY) and not path.startswith(OPEN_TO_EVERYONE):
            user = getattr(request, "user", None)
            if user is not None and user.is_authenticated and not is_platform_staff(user):
                from django.http import Http404

                raise Http404()
        return self.get_response(request)
