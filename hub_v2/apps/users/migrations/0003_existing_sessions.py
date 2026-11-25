"""Identify valid pre-existing sessions without changing their keys, contents or expiry."""
from django.conf import settings
from django.db import migrations
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac


def identify_sessions(apps, schema_editor):
    from importlib import import_module
    Store = import_module(settings.SESSION_ENGINE).SessionStore
    Session = apps.get_model("sessions", "Session")
    Tracked = apps.get_model("users", "UserSession")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    Profile = apps.get_model("users", "UserProfile")
    Terms = apps.get_model("orgs", "TermsAcceptance")
    for user in User.objects.filter(is_active=False, memberships__isnull=True):
        if Terms.objects.filter(user_id=user.pk).exists():
            Profile.objects.get_or_create(user_id=user.pk, defaults={"signup_pending": True})
    for row in Session.objects.filter(expire_date__gt=timezone.now()).iterator(chunk_size=100):
        data = Store().decode(row.session_data)
        user = User.objects.filter(pk=data.get("_auth_user_id"), is_active=True).first() if str(data.get("_auth_user_id", "")).isdigit() else None
        if not user:
            continue
        valid = any(constant_time_compare(data.get("_auth_user_hash", ""), salted_hmac(
            "django.contrib.auth.models.AbstractBaseUser.get_session_auth_hash", user.password,
            secret=secret, algorithm="sha256").hexdigest()) for secret in [settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS])
        if valid:
            Tracked.objects.get_or_create(session_key=row.session_key, defaults={"user_id": user.pk,
                "expires_at": row.expire_date, "discovered": True, "device": "Sessão anterior à atualização"})


class Migration(migrations.Migration):
    dependencies = [("users", "0002_initial"), ("sessions", "0001_initial"), ("orgs", "0006_organizationpreferences_miracle_rules")]
    operations = [migrations.RunPython(identify_sessions, migrations.RunPython.noop)]
