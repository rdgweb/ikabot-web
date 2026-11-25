"""Revocation tombstones also reject stale cached or concurrently re-saved sessions."""
from datetime import timedelta
from importlib import import_module
from django.conf import settings
from django.utils import timezone
from .models import UserSession


def device_label(agent):
    text = (agent or "").lower()
    browser = next((label for marker, label in [("edg/", "Edge"), ("opr/", "Opera"), ("firefox/", "Firefox"), ("chrome/", "Chrome"), ("safari/", "Safari")] if marker in text), "Navegador não identificado")
    system = next((label for marker, label in [("android", "Android"), ("iphone", "iPhone"), ("ipad", "iPad"), ("windows", "Windows"), ("macintosh", "macOS"), ("linux", "Linux")] if marker in text), "Sistema não identificado")
    return f"{browser} · {system}"


def remember_session(request):
    user = getattr(request, "user", None)
    key = request.session.session_key
    if not user or not user.is_authenticated or not key:
        return None
    from apps.audit.services import client_ip
    session, _ = UserSession.objects.get_or_create(session_key=key, defaults={
        "user": user, "device": device_label(request.META.get("HTTP_USER_AGENT", "")),
        "ip": client_ip(request), "last_seen_at": timezone.now(), "expires_at": request.session.get_expiry_date()})
    if session.user_id != user.pk or session.revoked_at:
        return session
    if session.last_seen_at is None or session.last_seen_at < timezone.now()-timedelta(minutes=5):
        session.last_seen_at = timezone.now()
        # Use the actual stored expiry; browsing alone does not extend a Django session.
        from django.contrib.sessions.models import Session
        expires = Session.objects.filter(session_key=key).values_list("expire_date", flat=True).first()
        UserSession.objects.filter(pk=session.pk, revoked_at__isnull=True).update(
            last_seen_at=session.last_seen_at, expires_at=expires or session.expires_at,
            device=device_label(request.META.get("HTTP_USER_AGENT", "")), ip=client_ip(request))
    return session


def revoke(sessions, actor, reason="user"):
    from apps.audit.services import record
    store = import_module(settings.SESSION_ENGINE).SessionStore
    count = 0
    for session in sessions.filter(user=actor, revoked_at__isnull=True).iterator(chunk_size=100):
        changed = UserSession.objects.filter(pk=session.pk, user=actor, revoked_at__isnull=True).update(revoked_at=timezone.now(), reason=reason)
        if changed:
            # Keep the tombstone even if deleting the cached session fails.
            try:
                store(session_key=session.session_key).delete()
            except Exception:
                pass
            count += 1
    if count:
        record("user.sessions_revoked", user=actor, target=actor, details={"count": count, "reason": reason})
    return count


def revoke_others(request, reason="user"):
    remember_session(request)
    return revoke(UserSession.objects.exclude(session_key=request.session.session_key), request.user, reason)
