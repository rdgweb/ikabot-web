"""A new user of the hub joins the default organization (N-106, stage 1).

While the hub has one organization, the role follows the groups the user already has (the user
form saves the groups after the user, so the role is fixed again when they change).
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def join_default_organization(sender, instance, created, raw=False, **kwargs):
    if created and not raw and not getattr(instance, "_own_organization", False):
        # N-106: the platform's team joins the default organization; an invited customer never does
        from .services import ensure_membership

        ensure_membership(instance)


@receiver(m2m_changed, sender=get_user_model().groups.through)
def follow_groups(sender, instance, action, reverse=False, **kwargs):
    if reverse or action not in ("post_add", "post_remove", "post_clear"):
        return
    from .models import Membership
    from .services import default_organization, role_for

    Membership.objects.filter(organization=default_organization(), user=instance).exclude(
        role=role_for(instance),
    ).update(role=role_for(instance))
