"""Minha organização and Minha conta (N-126): what the owner manages, and what each person sets for themselves."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import Http404
from django.shortcuts import redirect
from django.utils import timezone
from django.views.generic import TemplateView

from .models import Invite, Membership
from .services import create_member_invite, role_of


class OrgPage(LoginRequiredMixin, TemplateView):
    """A page of Minha organização: the tabs, the person's role, only with an organization."""

    tab = ""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and getattr(request, "organization", None) is None:
            raise Http404()
        return super().dispatch(request, *args, **kwargs)

    @property
    def role(self) -> str:
        if self.request.user.is_superuser:
            return Membership.OWNER
        return role_of(self.request.user, self.request.organization)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({"organization": self.request.organization, "tab": self.tab, "role": self.role,
                    "is_owner": self.role == Membership.OWNER,
                    "can_run": self.role in (Membership.OWNER, Membership.OPERATOR)})
        return ctx


class OverviewView(OrgPage):
    """GET/POST /organizacao/ — the organization at a glance; the owner renames it."""

    template_name = "orgs/overview.html"
    tab = "overview"

    def get_context_data(self, **kwargs):
        from apps.accounts.models import Account, Node
        from apps.billing import plans
        from apps.billing.services import account_for, active_subaccounts, state_of

        ctx = super().get_context_data(**kwargs)
        organization = self.request.organization
        account = account_for(organization)
        nodes = list(Node.objects.all())
        ctx.update({
            "plan": state_of(account), "plan_account": account,
            "plan_price": plans.brl(account.price) if account.price else "",
            "active": active_subaccounts(organization),
            "accounts": Account.objects.count(),
            "nodes": len(nodes), "online": sum(1 for node in nodes if node.is_online),
            "members": Membership.objects.filter(organization=organization).count(),
        })
        return ctx

    def post(self, request):
        from apps.audit.services import record

        if self.role != Membership.OWNER:
            raise Http404()
        name = (request.POST.get("name") or "").strip()[:120]
        if name:
            organization = request.organization
            old = organization.name
            organization.name = name
            organization.save(update_fields=["name", "updated_at"])
            record("organization.renamed", target=organization, target_label=f"{old} → {name}")
            messages.success(request, "Nome salvo.")
        return redirect("orgs:overview")


class SubaccountsView(OrgPage):
    """GET/POST /organizacao/subcontas/ — which sub-accounts work, within what the plan covers."""

    template_name = "orgs/subaccounts.html"
    tab = "subaccounts"

    def get_context_data(self, **kwargs):
        from apps.accounts.models import Account
        from apps.billing.services import active_subaccounts, state_for

        ctx = super().get_context_data(**kwargs)
        groups = []
        for account in Account.objects.prefetch_related("game_accounts").order_by("label"):
            worlds = sorted(account.game_accounts.all(), key=lambda ga: (ga.server_language, ga.server_number, ga.name))
            groups.append({"account": account, "worlds": worlds})
        state = state_for(self.request.organization)
        ctx.update({"groups": groups, "active": active_subaccounts(self.request.organization), "limit": state.limit})
        return ctx

    def post(self, request):
        from apps.accounts.models import GameAccount
        from apps.audit.services import record
        from apps.billing.services import limit_message, room_for

        if self.role not in (Membership.OWNER, Membership.OPERATOR):
            raise Http404()
        world = GameAccount.objects.select_related("account").filter(pk=request.POST.get("world")).first()
        if world is None:
            raise Http404()
        if not world.active:
            if world.blocked:
                messages.error(request, "Esta subconta está bloqueada pela Gameforge.")
                return redirect("orgs:subaccounts")
            if world.account.active and not room_for(request.organization, 1):
                messages.error(request, limit_message(request.organization))
                return redirect("orgs:subaccounts")
        world.active = not world.active
        world.save(update_fields=["active", "updated_at"])
        record("gameaccount.updated", target=world, target_label=f"{world.name or world.server_id}: {'ativa' if world.active else 'pausada'}")
        return redirect("orgs:subaccounts")


class MembersView(OrgPage):
    """GET/POST /organizacao/membros/ — who is in, with which role; the owner invites and removes."""

    template_name = "orgs/members.html"
    tab = "members"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        organization = self.request.organization
        ctx.update({
            "members": Membership.objects.filter(organization=organization).select_related("user").order_by("role", "user__username"),
            "pending": Invite.objects.filter(organization=organization, used_at__isnull=True, expires_at__gt=timezone.now())
                                     .exclude(role=""),
            "roles": Membership.ROLE_CHOICES, "new_link": self.request.session.pop("member-invite-link", ""),
            "email_ready": settings.EMAIL_READY,
        })
        return ctx

    def post(self, request):
        from apps.audit.services import record

        if self.role != Membership.OWNER:
            raise Http404()
        organization = request.organization
        action = request.POST.get("action")
        valid_roles = {code for code, _label in Membership.ROLE_CHOICES}
        if action == "invite":
            email = (request.POST.get("email") or "").strip().lower()
            role = request.POST.get("role") or Membership.OPERATOR
            try:
                validate_email(email)
            except ValidationError:
                messages.error(request, "Informe um e-mail válido.")
                return redirect("orgs:members")
            if role not in valid_roles:
                return redirect("orgs:members")
            if get_user_model().objects.filter(email__iexact=email).exists():
                messages.error(request, "Este e-mail já tem conta no IkaHub. Por enquanto cada pessoa fica em uma organização só.")
                return redirect("orgs:members")
            invite, token = create_member_invite(organization=organization, email=email, role=role, created_by=request.user)
            link = (settings.SITE_URL or settings.HUB_PUBLIC_URL or request.build_absolute_uri("/").rstrip("/")) + f"/convite/{token}/"
            sent = _send_invite(request, organization, email, link)
            request.session["member-invite-link"] = "" if sent else link
            record("member.invited", target=invite, target_label=f"{email} · {dict(Membership.ROLE_CHOICES)[role]}")
            messages.success(request, f"Convite enviado para {email}." if sent else "Convite criado. Copie o link abaixo e envie.")
        elif action == "role":
            member = Membership.objects.filter(organization=organization, pk=request.POST.get("member")).select_related("user").first()
            role = request.POST.get("role")
            if member and role in valid_roles and not _last_owner(member, role):
                member.role = role
                member.save(update_fields=["role", "updated_at"])
                record("membership.changed", target=member.user, target_label=f"{member.user.get_username()}: {member.get_role_display()}")
            elif member:
                messages.error(request, "A organização precisa de pelo menos um dono.")
        elif action == "remove":
            member = Membership.objects.filter(organization=organization, pk=request.POST.get("member")).select_related("user").first()
            if member and member.user_id != request.user.pk and not _last_owner(member, ""):
                user = member.user
                member.delete()
                if not user.memberships.exists():
                    user.is_active = False                    # without an organization the account has nothing to open
                    user.save(update_fields=["is_active"])
                record("member.removed", target=user, target_label=user.get_username())
                messages.success(request, f"{user.get_username()} saiu da organização.")
        elif action == "cancel":
            Invite.objects.filter(organization=organization, pk=request.POST.get("invite"), used_at__isnull=True).update(expires_at=timezone.now())
        return redirect("orgs:members")


def _last_owner(member, new_role: str) -> bool:
    """Would this change leave the organization without an owner?"""
    if member.role != Membership.OWNER or new_role == Membership.OWNER:
        return False
    return not Membership.objects.filter(organization=member.organization, role=Membership.OWNER).exclude(pk=member.pk).exists()


def _send_invite(request, organization, email: str, link: str) -> bool:
    if not settings.EMAIL_READY:
        return False
    from django.core.mail import send_mail

    body = (f"Olá!\n\n{request.user.get_username()} convidou você para a organização {organization.name} no IkaHub.\n\n"
            f"Para entrar, abra o link abaixo (vale 7 dias e funciona uma vez):\n\n{link}\n\nIkaHub")
    try:
        send_mail(f"Convite para {organization.name} no IkaHub", body, None, [email])
        return True
    except Exception:  # noqa: BLE001 - the link is shown instead
        return False


class AccountView(LoginRequiredMixin, TemplateView):
    """GET/POST /conta/ — the person's own name, e-mail, password and the tour."""

    template_name = "orgs/account.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("password_form", PasswordChangeForm(self.request.user))
        ctx["organization"] = getattr(self.request, "organization", None)
        ctx["role_label"] = dict(Membership.ROLE_CHOICES).get(role_of(self.request.user, ctx["organization"]), "")
        return ctx

    def post(self, request):
        from apps.audit.services import record
        from apps.settings_app.tour import restart

        user = request.user
        action = request.POST.get("action")
        if action == "profile":
            name = (request.POST.get("first_name") or "").strip()[:150]
            email = (request.POST.get("email") or "").strip().lower()
            try:
                validate_email(email)
            except ValidationError:
                messages.error(request, "Informe um e-mail válido.")
                return redirect("account")
            if get_user_model().objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                messages.error(request, "Este e-mail já é usado por outra conta.")
                return redirect("account")
            user.first_name, user.email = name, email
            user.save(update_fields=["first_name", "email"])
            record("user.updated", target=user, target_label=user.get_username())
            messages.success(request, "Dados salvos.")
        elif action == "password":
            form = PasswordChangeForm(user, request.POST)
            if not form.is_valid():
                return self.render_to_response(self.get_context_data(password_form=form))
            form.save()
            update_session_auth_hash(request, form.user)
            record("user.password", target=user, target_label=user.get_username())
            messages.success(request, "Senha trocada.")
        elif action == "tour":
            restart(user)
            return redirect("/")
        return redirect("account")
