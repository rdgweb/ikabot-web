"""Beta by invitation (N-106): accepting an invitation, the team's list of invitations (the terms are on the site, N-119)."""
from django.contrib.auth import get_user_model, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View

from .models import Invite
from .services import TERMS_VERSION, accept_invite, create_invite, find_invite


class InviteAcceptView(View):
    """GET/POST /convite/<token>/ — public, only with a usable invitation."""

    template_name = "orgs/invite_accept.html"

    def get(self, request, token):
        invite = find_invite(token)
        errors = ["Saia da sua conta para usar este convite."] if invite and request.user.is_authenticated else []
        return render(request, self.template_name, {"invite": invite, "errors": errors})

    def post(self, request, token):
        from apps.audit.services import client_ip, record

        invite = find_invite(token)
        if invite is None:
            return render(request, self.template_name, {"invite": None}, status=410)
        if request.user.is_authenticated:
            return render(request, self.template_name, {"invite": invite, "errors": ["Saia da sua conta para usar este convite."]})
        User = get_user_model()
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password1") or ""
        errors = []
        try:
            User.username_validator(username)
        except ValidationError:
            errors.append("Usuário: use letras, números e @/./+/-/_ (sem espaços).")
        if not username or User.objects.filter(username__iexact=username).exists():
            errors.append("Este usuário já existe; escolha outro.")
        if password != (request.POST.get("password2") or ""):
            errors.append("As senhas não conferem.")
        else:
            try:
                validate_password(password, user=User(username=username, email=invite.email))
            except ValidationError as exc:
                errors.extend(exc.messages)
        if request.POST.get("accept_terms") != "1":
            errors.append("É preciso aceitar os termos de uso.")
        if errors:
            return render(request, self.template_name, {"invite": invite, "errors": errors, "username": username})

        user, organization = accept_invite(invite, username=username, password=password, ip=client_ip(request),
                                           user_agent=request.META.get("HTTP_USER_AGENT", ""))
        record("invite.accepted", user=user, target=organization, target_label=organization.name,
               organization=organization, details={"termos": TERMS_VERSION})
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return redirect("/")


class InviteManageView(LoginRequiredMixin, View):
    """GET/POST /users/convites/ — the platform's team creates invitations."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not (request.user.is_staff or request.user.is_superuser):
            raise Http404()
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        from apps.website.models import InviteRequest

        new_link = request.session.pop("invite-link", "")
        return render(request, "orgs/invites.html", {
            "invites": Invite.objects.all()[:200], "new_link": new_link, "version": TERMS_VERSION, "now": timezone.now(),
            "requests": InviteRequest.objects.filter(handled_at__isnull=True)[:100],   # asked on the site (N-119)
            "prefill": {"email": request.GET.get("email", "")[:254], "name": request.GET.get("name", "")[:120]},
        })

    def post(self, request):
        from django.core.validators import validate_email

        from apps.audit.services import record

        if request.POST.get("handled"):
            from apps.website.models import InviteRequest

            InviteRequest.objects.filter(pk=request.POST["handled"], handled_at__isnull=True).update(handled_at=timezone.now())
            return redirect("invites")
        email = (request.POST.get("email") or "").strip()
        name = (request.POST.get("organization_name") or "").strip()
        try:
            validate_email(email)
        except ValidationError:
            return redirect("invites")
        if not name:
            return redirect("invites")
        invite, token = create_invite(email=email, organization_name=name, created_by=request.user)
        request.session["invite-link"] = request.build_absolute_uri(f"/convite/{token}/")
        record("invite.created", target=invite, target_label=f"{email} · {name}")
        return redirect("invites")
