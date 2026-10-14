"""Organization helpers (N-106, stage 1).

Until signing up exists, everything the hub creates belongs to the default organization: the
root models take default_organization_id() as their default, so no creation path has to change.
"""
from __future__ import annotations

DEFAULT_SLUG = "principal"
DEFAULT_NAME = "Principal"


def default_organization():
    from .models import Organization

    organization = Organization.objects.filter(slug=DEFAULT_SLUG).first()
    if organization is None:
        organization, _ = Organization.objects.get_or_create(slug=DEFAULT_SLUG, defaults={"name": DEFAULT_NAME})
    return organization


def default_organization_id():
    """Default of the organization column of the root models (one query when a row is created)."""
    return default_organization().pk


def role_for(user) -> str:
    """The role a user of the single-operator hub gets: the groups it already has decide."""
    from .models import Membership

    if user.is_superuser or user.is_staff:
        return Membership.OWNER
    groups = set(user.groups.values_list("name", flat=True))
    if "admin" in groups:
        return Membership.OWNER
    if "viewer" in groups and "operator" not in groups:
        return Membership.VIEWER
    return Membership.OPERATOR   # today a logged user without group can do everything an operator does


def ensure_membership(user):
    from .models import Membership

    membership, _ = Membership.objects.get_or_create(
        organization=default_organization(), user=user, defaults={"role": role_for(user)},
    )
    return membership
