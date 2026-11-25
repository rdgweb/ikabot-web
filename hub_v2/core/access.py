"""The platform and organization roles share one policy; legacy groups grant no access."""

def authenticated(user):
    return bool(user and user.is_authenticated and user.is_active)


def platform_admin(user):
    if not authenticated(user):
        return False
    from apps.access_control.policy import permissions
    return any(p.startswith("platform.") for p in permissions(user))


def platform_owner(user):
    return authenticated(user) and bool(user.is_superuser)


def organization_role(user, organization=None):
    if not authenticated(user):
        return ""
    if organization is None:
        from apps.orgs.scope import current_organization
        organization = current_organization()
    if organization is None:
        from apps.orgs.scope import organization_for
        organization = organization_for(user)
    if organization is None:
        return ""
    from apps.access_control.models import AccessMode
    if AccessMode.objects.filter(user=user, scope_key=str(organization.pk)).exists():
        from apps.access_control.policy import permissions
        from apps.access_control.catalog import OWNER
        caps = permissions(user, organization)
        # Compatibility for existing view guards. The route middleware checks the exact capability.
        if OWNER <= caps:
            return "owner"
        return "operator" if any(p.startswith("organization.") and p.endswith(".manage") for p in caps) else "viewer"
    return user.memberships.filter(organization=organization).values_list("role", flat=True).first() or ""


def operator(user, organization=None):
    if not authenticated(user):
        return False
    if organization is None:
        from apps.orgs.scope import current_organization, organization_for
        organization = current_organization() or organization_for(user)
    from apps.access_control.policy import permissions
    return any(p.startswith("organization.") and p.endswith(".manage") for p in permissions(user, organization))


def owner(user, organization=None):
    return platform_admin(user) or organization_role(user, organization) == "owner"
