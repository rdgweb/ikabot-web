from django.contrib.auth import logout
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from .models import UserSession
from .session_services import remember_session


class AccountSessionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        key = request.session.session_key
        previous_user_id = request.user.pk if request.user.is_authenticated else None
        if request.user.is_authenticated and key:
            tracked = UserSession.objects.filter(session_key=key).only("user_id", "revoked_at").first()
            if tracked and (tracked.revoked_at or tracked.user_id != request.user.pk):
                logout(request)
                if request.headers.get("HX-Request") == "true":
                    return HttpResponse(status=204, headers={"HX-Redirect": "/login/"})
                return redirect("login")
        response = self.get_response(request)
        if key and previous_user_id and key != request.session.session_key:
            UserSession.objects.filter(session_key=key, user_id=previous_user_id, revoked_at__isnull=True).update(
                revoked_at=timezone.now(), reason="rotated")
        if response.status_code < 500 and getattr(request, "user", None) and request.user.is_authenticated:
            tracked = remember_session(request)
            # A revocation committed during the request must not produce a new authenticated cookie.
            if tracked and (tracked.revoked_at or tracked.user_id != request.user.pk):
                logout(request)
        return response
