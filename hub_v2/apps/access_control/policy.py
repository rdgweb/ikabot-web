"""Fresh decisions per request; expiry and revocation take effect without cache invalidation."""
from django.db.models import Q
from django.utils import timezone
from .catalog import CATALOG, OWNER, OPERATOR, VIEWER, LEGACY_PLATFORM
from .models import AccessMode, Grant
from contextvars import ContextVar

_cache = ContextVar("access_decisions", default=None)


def sources(user, organization=None):
    if not user or not user.is_authenticated or not user.is_active:
        return []
    if user.is_superuser:
        return [{"name": "Administrador principal", "permissions": set(CATALOG), "expires_at": None}]
    keys = ["platform"] + ([str(organization.pk)] if organization else [])
    managed = set(AccessMode.objects.filter(user=user, scope_key__in=keys).values_list("scope_key", flat=True))
    result = []
    if user.is_staff and "platform" not in managed:
        result.append({"name": "Administrador da plataforma (legado)", "permissions": LEGACY_PLATFORM | OPERATOR, "expires_at": None})
    member = user.memberships.filter(organization=organization).first() if organization else None
    if organization and str(organization.pk) not in managed:
        if member:
            result.append({"name": f"Membro · {member.get_role_display()} (legado)",
                           "permissions": {"owner": OWNER, "operator": OPERATOR, "viewer": VIEWER}.get(member.role, set()), "expires_at": None})
    for grant in Grant.objects.filter(user=user, scope_key__in=keys, revoked_at__isnull=True).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())).select_related("role"):
        role = grant.role
        if role.scope == "platform" and grant.scope_key == "platform" and grant.organization_id is None and role.organization_id is None:
            prefix = "platform."
        elif organization and member and organization.active and role.scope == "organization" and grant.organization_id == organization.pk and role.organization_id in (None, organization.pk):
            prefix = "organization."
        else:
            continue
        result.append({"name": role.name, "permissions": {p for p in role.permissions if p in CATALOG and p.startswith(prefix)},
                       "expires_at": grant.expires_at, "grant": grant})
    return result


def permissions(user, organization=None):
    cache = _cache.get()
    cache_key = (getattr(user, "pk", None), getattr(organization, "pk", None))
    if cache is not None and cache_key in cache:
        return cache[cache_key]
    result = set().union(*(source["permissions"] for source in sources(user, organization)))
    if cache is not None:
        cache[cache_key] = result
    return result


def allowed(user, permission, organization=None):
    return permission in permissions(user, organization)
