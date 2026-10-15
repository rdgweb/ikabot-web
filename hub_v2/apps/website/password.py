"""Forgot my password (N-125): Django's one-use links, sent by the owner's SMTP, in the public layout."""
from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy


class ResetRequestView(auth_views.PasswordResetView):
    template_name = "registration/reset_request.html"
    email_template_name = "registration/reset_email.txt"
    subject_template_name = "registration/reset_subject.txt"
    success_url = reverse_lazy("password-reset-sent")

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "email_ready": settings.EMAIL_READY}

    def form_valid(self, form):
        if not settings.EMAIL_READY:
            return self.form_invalid(form)
        # links point to the published address, never to whatever Host the request carried
        self.extra_email_context = {"base": settings.SITE_URL or settings.HUB_PUBLIC_URL}
        return super().form_valid(form)


class ResetSentView(auth_views.PasswordResetDoneView):
    template_name = "registration/reset_sent.html"


class ResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "registration/reset_confirm.html"
    success_url = reverse_lazy("password-reset-done")

    def form_valid(self, form):
        from apps.audit.services import record

        response = super().form_valid(form)
        record("user.password_reset", user=form.user, target=form.user, target_label=form.user.get_username())
        return response


class ResetDoneView(auth_views.PasswordResetCompleteView):
    template_name = "registration/reset_done.html"


def site_flags(request):
    """Template flag for the login page: is open sign-up on?"""
    from apps.orgs.services import signup_available

    if request.path != "/login/":
        return {}
    return {"signup_open": signup_available()}
