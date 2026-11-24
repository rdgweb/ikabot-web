"""The platform and organization roles share one policy; legacy groups grant no access."""

def authenticated(user):
    return bool(user and user.is_authenticated and user.is_active)


def platform_admin(user):
    return authenticated(user) and bool(user.is_staff or user.is_superuser)


def platform_owner(user):
    return authenticated(user) and bool(user.is_superuser)


def organization_role(user, organization=None):
    if not authenticated(user):
        return ""
    if organization is None:
        from apps.orgs.scope import organization_for
        organization = organization_for(user)
    if organization is None:
        return ""
    return user.memberships.filter(organization=organization).values_list("role", flat=True).first() or ""


def operator(user, organization=None):
    return platform_admin(user) or organization_role(user, organization) in ("owner", "operator")


def owner(user, organization=None):
    return platform_admin(user) or organization_role(user, organization) == "owner"

