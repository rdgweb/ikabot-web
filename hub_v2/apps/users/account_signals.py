from django.conf import settings
from django.contrib.auth.signals import user_logged_out
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from django.utils import timezone


@receiver(user_logged_out)
def mark_logout(sender, request, user, **kwargs):
    if request is not None and request.session.session_key:
        from .models import UserSession
        UserSession.objects.filter(session_key=request.session.session_key, revoked_at__isnull=True).update(revoked_at=timezone.now(), reason="logout")


@receiver(pre_save, sender=settings.AUTH_USER_MODEL)
def remember_password(sender, instance, raw=False, update_fields=None, **kwargs):
    if raw or not instance.pk or (update_fields is not None and "password" not in update_fields):
        instance._hub_password_changed = False
        return
    previous = sender.objects.filter(pk=instance.pk).values_list("password", flat=True).first()
    instance._hub_password_changed = previous is not None and previous != instance.password


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def invalidate_password_sessions(sender, instance, raw=False, **kwargs):
    if not raw and getattr(instance, "_hub_password_changed", False):
        from .models import EmailChallenge, UserSession
        from .session_services import revoke
        revoke(UserSession.objects.all(), instance, "password_change")
        EmailChallenge.objects.filter(user=instance, used_at__isnull=True).update(used_at=timezone.now())
        instance._hub_password_changed = False
