"""Legacy creation joins the default organization with least privilege.

Explicit organization creation/invitations opt out; Membership is authoritative.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def join_default_organization(sender, instance, created, raw=False, **kwargs):
    if created and not raw and not getattr(instance, "_own_organization", False):
        # N-106: the platform's team joins the default organization; an invited customer never does
        from .services import ensure_membership

        ensure_membership(instance)


# Membership is authoritative. Editing legacy groups must never change a role.
