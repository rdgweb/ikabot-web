from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.views import View

from apps.audit.services import record
from apps.orgs.models import Membership
from apps.settings_app.models import AppSetting
from core.access import platform_owner


class AccessManagementView(View):
    def dispatch(self, request, *args, **kwargs):
        if not platform_owner(request.user):
            raise Http404()
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return render(request, "users/access.html", {
            "people": get_user_model().objects.order_by("username"),
            "memberships": Membership.objects.select_related("user", "organization").order_by("organization__name", "user__username"),
            "roles": Membership.ROLE_CHOICES,
        })

    def post(self, request):
        User = get_user_model()
        try:
            with transaction.atomic():
                # Serialize changes so two tabs cannot remove the final owner.
                AppSetting.objects.get_or_create(pk="access_management_lock")
                AppSetting.objects.select_for_update().get(pk="access_management_lock")
                if request.POST.get("action") == "platform":
                    user = User.objects.select_for_update().get(pk=request.POST.get("user_id"))
                    role = request.POST.get("role")
                    if user.is_superuser or role not in ("organization", "platform"):
                        raise ValueError("O administrador principal é protegido. Selecione um papel válido para outro usuário.")
                    before = user.is_staff
                    user.is_staff = role == "platform"
                    user.save(update_fields=["is_staff"])
                    record("access.platform_changed", target=user, user=request.user,
                           details={"before_staff": before, "after_staff": user.is_staff})
                elif request.POST.get("action") == "membership":
                    member = Membership.objects.select_for_update().get(pk=request.POST.get("membership_id"))
                    role = request.POST.get("role")
                    if role not in dict(Membership.ROLE_CHOICES):
                        raise ValueError("Papel inválido.")
                    if member.role == Membership.OWNER and role != Membership.OWNER and not Membership.objects.filter(
                            organization=member.organization, role=Membership.OWNER, user__is_active=True).exclude(pk=member.pk).exists():
                        raise ValueError("A organização precisa manter pelo menos um dono.")
                    before = member.role
                    member.role = role
                    member.save(update_fields=["role", "updated_at"])
                    record("membership.changed", target=member, user=request.user,
                           details={"before": before, "after": role}, organization=member.organization)
                else:
                    raise ValueError("Ação inválida.")
            messages.success(request, "Permissão atualizada. A nova regra vale na próxima requisição.")
        except (ValueError, TypeError, ValidationError, User.DoesNotExist, Membership.DoesNotExist):
            messages.error(request, "Não foi possível alterar. Confira o papel e preserve o administrador principal e o último dono da organização.")
        return redirect("users:access")
