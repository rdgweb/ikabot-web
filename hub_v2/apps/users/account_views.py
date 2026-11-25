from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView
from apps.orgs.models import Membership
from apps.orgs.services import role_of
from .account_forms import EmailChangeForm, ProfileForm
from .account_services import confirm_email, profile_for, request_email_confirmation, valid_challenge
from .models import EmailChallenge, UserProfile, UserSession
from .session_services import remember_session, revoke, revoke_others


class AccountView(LoginRequiredMixin, TemplateView):
    template_name = "orgs/account.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.headers.get("HX-Request") == "true":
            return HttpResponse(status=204, headers={"HX-Redirect": request.get_full_path()})
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        profile = profile_for(user)
        tab = self.request.GET.get("tab", "profile")
        if tab not in ("profile", "security", "preferences"):
            tab = "profile"
        ctx.update({"account_profile": profile, "account_tab": tab, "email_verified": profile.email_is_verified(),
                    "email_ready": settings.EMAIL_READY, "organization": getattr(self.request, "organization", None),
                    "pending_email": EmailChallenge.objects.filter(user=user, used_at__isnull=True, expires_at__gt=timezone.now()).first()})
        ctx["role_label"] = dict(Membership.ROLE_CHOICES).get(role_of(user, ctx["organization"]), "")
        ctx.setdefault("profile_form", ProfileForm(initial={"first_name": user.first_name, "last_name": user.last_name, "display_name": profile.display_name}))
        ctx.setdefault("email_form", EmailChangeForm(user))
        ctx.setdefault("password_form", PasswordChangeForm(user))
        if tab == "security":
            remember_session(self.request)
            sessions = UserSession.objects.filter(user=user, revoked_at__isnull=True, expires_at__gt=timezone.now())
            ctx["sessions"] = Paginator(sessions, 10).get_page(self.request.GET.get("page"))
            ctx["has_other_sessions"] = sessions.exclude(session_key=self.request.session.session_key).exists()
            ctx["session_rows"] = [{"id": item.pk, "device": item.device, "ip": item.ip,
                "first_seen": item.created_at, "last_seen": item.last_seen_at, "discovered": item.discovered,
                "current": item.session_key == self.request.session.session_key} for item in ctx["sessions"]]
        return ctx

    def post(self, request):
        from apps.audit.services import record
        action = request.POST.get("action")
        if action in ("password", "email", "revoke_session", "revoke_others", "resend_email"):
            from .account_services import sensitive_rate_limit
            try:
                sensitive_rate_limit(request.user)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
                return redirect("/conta/?tab=security")
        tab = "profile"
        if action == "profile":
            form = ProfileForm(request.POST, request.FILES)
            if not form.is_valid():
                return self.render_to_response(self.get_context_data(profile_form=form), status=400)
            with transaction.atomic():
                user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
                user.first_name, user.last_name = form.cleaned_data["first_name"], form.cleaned_data["last_name"]
                user.save(update_fields=["first_name", "last_name"])
                profile = profile_for(user)
                profile.display_name = form.cleaned_data["display_name"]
                if form.cleaned_data["avatar"] or form.cleaned_data["remove_avatar"]:
                    profile.avatar = form.cleaned_data["avatar"]
                    profile.avatar_version += 1
                profile.save(update_fields=["display_name", "avatar", "avatar_version", "updated_at"])
            record("user.updated", target=user, details={"area": "personal_profile"})
            messages.success(request, "Perfil salvo.")
        elif action == "password":
            with transaction.atomic():
                user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
                form = PasswordChangeForm(user, request.POST)
                if not form.is_valid():
                    request.GET = request.GET.copy()
                    request.GET["tab"] = "security"
                    return self.render_to_response(self.get_context_data(password_form=form), status=400)
                user = form.save()
            update_session_auth_hash(request, user)
            revoke_others(request, "password_change")
            record("user.password", target=user)
            messages.success(request, "Senha trocada. As outras sessões foram encerradas.")
            tab = "security"
        elif action in ("email", "verify_email", "resend_email"):
            tab = "security"
            try:
                if action == "email":
                    form = EmailChangeForm(request.user, request.POST)
                    if not form.is_valid():
                        request.GET = request.GET.copy(); request.GET["tab"] = tab
                        return self.render_to_response(self.get_context_data(email_form=form), status=400)
                    request_email_confirmation(request, form.cleaned_data["email"], form.cleaned_data["current_password"])
                else:
                    pending = EmailChallenge.objects.filter(user=request.user, used_at__isnull=True).first() if action == "resend_email" else None
                    if pending and pending.purpose == "change":
                        request_email_confirmation(request, pending.target_email, request.POST.get("current_password"))
                    else:
                        request_email_confirmation(request)
                messages.success(request, "E-mail enviado. Abra o link e confirme para concluir.")
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
        elif action == "cancel_email":
            EmailChallenge.objects.filter(user=request.user, used_at__isnull=True).update(used_at=timezone.now())
            record("user.email_cancelled", target=request.user)
            messages.success(request, "Solicitação cancelada. Seu e-mail atual foi mantido.")
            tab = "security"
        elif action in ("revoke_session", "revoke_others"):
            tab = "security"
            if not get_user_model().objects.get(pk=request.user.pk).check_password(request.POST.get("current_password", "")):
                messages.error(request, "Confirme sua senha atual para encerrar sessões.")
            elif action == "revoke_others":
                count = revoke_others(request)
                messages.success(request, f"{count} outra(s) sessão(ões) encerrada(s).")
            else:
                if not request.POST.get("session_id"):
                    messages.error(request, "Selecione a sessão que deseja encerrar.")
                    return redirect("/conta/?tab=security")
                try:
                    target = get_object_or_404(UserSession, pk=request.POST.get("session_id"), user=request.user)
                except (ValueError, ValidationError):
                    raise Http404()
                if target.session_key == request.session.session_key:
                    messages.error(request, "Esta é sua sessão atual. Use Sair para encerrá-la.")
                else:
                    revoke(UserSession.objects.filter(pk=target.pk), request.user)
                    messages.success(request, "Sessão encerrada.")
        elif action == "preferences":
            UserProfile.objects.update_or_create(user=request.user, defaults={
                "compact_tables": request.POST.get("compact_tables") == "on", "reduced_motion": request.POST.get("reduced_motion") == "on"})
            record("user.preferences", target=request.user)
            messages.success(request, "Preferências salvas.")
            tab = "preferences"
        elif action == "tour":
            from apps.settings_app.tour import restart
            restart(request.user)
            return redirect("/")
        else:
            raise Http404()
        return redirect("/conta/?tab="+tab)


class AccountAvatarView(LoginRequiredMixin, View):
    def get(self, request):
        avatar = UserProfile.objects.filter(user=request.user).values_list("avatar", flat=True).first()
        if not avatar:
            raise Http404()
        return HttpResponse(bytes(avatar), content_type="image/jpeg", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


class EmailConfirmView(LoginRequiredMixin, View):
    def get(self, request, token):
        challenge = valid_challenge(request.user, token)
        return render(request, "users/email_confirm.html", {"challenge": challenge}, status=200 if challenge else 410)

    def post(self, request, token):
        try:
            purpose = confirm_email(request, token)
        except ValidationError as exc:
            return render(request, "users/email_confirm.html", {"errors": exc.messages}, status=410)
        if purpose == "change":
            revoke_others(request, "email_change")
        messages.success(request, "E-mail confirmado." if purpose == "verify" else "Novo e-mail confirmado. As outras sessões foram encerradas.")
        return redirect("/conta/?tab=security")


class SignupResendView(View):
    def get(self, request):
        return render(request, "registration/signup_resend.html", {"email_ready": settings.EMAIL_READY})

    def post(self, request):
        from .account_services import resend_signup
        resend_signup(request, request.POST.get("email", ""))
        return render(request, "registration/signup_resend.html", {"sent": True, "email_ready": settings.EMAIL_READY})
