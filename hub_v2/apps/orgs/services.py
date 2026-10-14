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
    """Default of the organization column of the root models: the organization of the request
    (stage 3a), or the default one for what the platform creates by itself (agent, commands)."""
    from .scope import current_organization

    organization = current_organization()
    return organization.pk if organization is not None else default_organization().pk


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


def preferences_of(organization):
    """The organization's own choices (created empty: everything follows the platform)."""
    from .models import OrganizationPreferences

    if organization is None:
        return None
    preferences, _ = OrganizationPreferences.objects.get_or_create(organization=organization)
    return preferences


def captcha_goes_to_telegram(organization, platform_config) -> bool:
    """After the automatic solver failed: send the captcha to the organization's chat?"""
    preferences = preferences_of(organization)
    choice = preferences.captcha_telegram if preferences else ""
    if choice == "on":
        return True
    if choice == "off":
        return False
    return platform_config.solve_method == "telegram" or platform_config.fallback_method == "telegram"


TERMS_VERSION = "beta-2026-10-14b"   # b: plans and payment (N-124); nobody had accepted the first
INVITE_DAYS = 7


def create_invite(*, email: str, organization_name: str, created_by) -> tuple[object, str]:
    """A one-use invitation; returns it and the link token (shown once, kept only as a hash)."""
    import secrets
    from datetime import timedelta

    from django.utils import timezone

    from core.auth.tokens import hash_agent_token

    from .models import Invite

    token = secrets.token_urlsafe(32)
    invite = Invite.objects.create(
        email=email.strip().lower(), organization_name=organization_name.strip(), token_hash=hash_agent_token(token),
        created_by=created_by if getattr(created_by, "pk", None) else None,
        expires_at=timezone.now() + timedelta(days=INVITE_DAYS),
    )
    return invite, token


def find_invite(token: str):
    """The invitation behind a link, if it is still usable."""
    from django.utils import timezone

    from core.auth.tokens import hash_agent_token

    from .models import Invite

    if not token or len(token) > 100:
        return None
    invite = Invite.objects.filter(token_hash=hash_agent_token(token)).first()
    if invite is None or invite.used_at is not None or invite.expires_at <= timezone.now():
        return None
    return invite


def accept_invite(invite, *, username: str, password: str, ip=None, user_agent: str = ""):
    """Create the person, their own organization (they own it) and the record of the accepted terms."""
    from django.contrib.auth import get_user_model
    from django.db import transaction
    from django.utils import timezone
    from django.utils.text import slugify

    from .models import Membership, Organization, TermsAcceptance

    User = get_user_model()
    with transaction.atomic():
        user = User(username=username, email=invite.email)
        user.set_password(password)
        user._own_organization = True
        user.save()
        base = slugify(invite.organization_name)[:40] or "organizacao"
        slug = base
        while Organization.objects.filter(slug=slug).exists():
            slug = f"{base}-{__import__('secrets').token_hex(3)}"
        organization = Organization.objects.create(name=invite.organization_name, slug=slug)
        Membership.objects.create(organization=organization, user=user, role=Membership.OWNER)
        TermsAcceptance.objects.create(user=user, version=TERMS_VERSION, ip=ip, user_agent=(user_agent or "")[:200])
        invite.used_at = timezone.now()
        invite.used_by = user
        invite.organization = organization
        invite.save(update_fields=["used_at", "used_by", "organization", "updated_at"])
    return user, organization
