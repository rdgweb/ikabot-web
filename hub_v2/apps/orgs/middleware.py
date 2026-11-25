from . import scope


class OrganizationScopeMiddleware:
    """A logged person's request only reaches their organization's data (N-106, stage 3a)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from apps.access_control.policy import _cache
        access_token = _cache.set({})
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            request.organization = None
            # no person: no scope — but the agent API may set its node's scope further in; the reset
            # below guarantees it never outlives this request
            token = scope._current.set(None)
        else:
            organization = scope.organization_for(user, request.session.get("active_organization"))
            request.organization = organization
            token = scope.activate(organization, deny=organization is None)
        try:
            return self.get_response(request)
        finally:
            scope.deactivate(token)
            _cache.reset(access_token)


# N-106: pages of the platform itself (internal notes, global settings and integrations, users,
# platform proxies). A customer never reaches them; for them the page does not exist.
PLATFORM_ONLY = (
    "/django-admin/", "/api/schema/", "/api/docs/", "/notes/", "/settings/", "/users/", "/captcha/",
    "/telegram/", "/proxy/", "/telemetry/", "/accounts/docker-hosts/",
)
OPEN_TO_EVERYONE = ("/settings/tour/done/", "/settings/tour/state/")   # the switch (tour/toggle/) is the platform's


def is_platform_staff(user) -> bool:
    from core.access import platform_admin
    return platform_admin(user)


class PlatformPagesMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        from core.access import platform_owner
        if (path.startswith("/django-admin/") or
                (path.startswith("/users/") and not path.startswith(("/users/convites/", "/users/permissoes/")))):
            if not platform_owner(getattr(request, "user", None)):
                from django.http import Http404
                raise Http404()
        if path.startswith(PLATFORM_ONLY) and not path.startswith(OPEN_TO_EVERYONE):
            user = getattr(request, "user", None)
            if user is not None and user.is_authenticated and not is_platform_staff(user):
                from django.http import Http404

                raise Http404()
        return self.get_response(request)
