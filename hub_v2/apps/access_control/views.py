from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render, get_object_or_404
from django.views import View
from apps.orgs.models import Organization
from .catalog import CATALOG
from .models import AccessEvent, Grant, Role
from .policy import allowed, permissions, sources
from .services import confirm, preview


class AccessManagementView(View):
    organization_scope = False

    def dispatch(self, request, *args, **kwargs):
        self.organization = request.organization if self.organization_scope else None
        self.scope = "organization" if self.organization_scope else "platform"
        if self.organization_scope and self.organization is None:
            raise Http404()
        if not allowed(request.user, f"{self.scope}.access.view", self.organization):
            raise Http404()
        return super().dispatch(request, *args, **kwargs)

    def context(self, request):
        q = request.GET.get("q", "").strip()[:100]
        tab = request.GET.get("tab", "people")
        people = get_user_model().objects.order_by("username", "pk")
        roles = Role.objects.filter(scope=self.scope)
        events = AccessEvent.objects.all()
        organizations = Organization.objects.all().order_by("name", "pk")
        if self.organization:
            people = people.filter(memberships__organization=self.organization)
            roles = roles.filter(Q(organization=self.organization) | Q(organization__isnull=True))
            events = events.filter(organization=self.organization)
            organizations = organizations.filter(pk=self.organization.pk)
        else:
            roles = roles.filter(organization__isnull=True)
        selected = get_object_or_404(people, pk=request.GET["person"]) if request.GET.get("person", "").isdigit() else None
        grants = Grant.objects.filter(user=selected, scope_key=str(self.organization.pk) if self.organization else "platform", revoked_at__isnull=True).select_related("role") if selected else Grant.objects.none()
        role = roles.filter(pk=request.GET["role"]).first() if request.GET.get("role") else None
        queryset = {"people": people.filter(Q(username__icontains=q) | Q(email__icontains=q)),
                    "roles": roles.filter(name__icontains=q),
                    "organizations": organizations.filter(name__icontains=q),
                    "history": events.filter(Q(actor_label__icontains=q) | Q(target_label__icontains=q)).select_related("organization")}.get(tab)
        if queryset is None:
            tab, queryset = "people", people
        choices = permissions(request.user, self.organization)
        effective = sources(selected, self.organization) if selected else []
        for source in effective:
            source["labels"] = [CATALOG[p] for p in sorted(source["permissions"])]
        return {"tab": tab, "q": q, "page_obj": Paginator(queryset, 25).get_page(request.GET.get("page")),
                "selected_person": selected, "effective_sources": effective,
                "grants": grants, "editing_role": role,
                "permission_choices": [(p, label) for p, label in CATALOG.items() if p.startswith(self.scope + ".") and p in choices],
                "can_manage": allowed(request.user, f"{self.scope}.access.manage", self.organization),
                "scope": self.scope, "organization": self.organization, "organization_scope": self.organization_scope,
                "role_choices": roles.filter(name__icontains=request.GET.get("role_q", "")[:100])[:25]}

    def get(self, request):
        try:
            context = self.context(request)
        except (ValueError, ValidationError):
            raise Http404()
        return render(request, "access_control/index.html", context)

    def post(self, request):
        try:
            if request.POST.get("confirmation"):
                confirm(request.user, request.POST["confirmation"], self.organization)
                messages.success(request, "Acesso salvo. A alteração vale na próxima requisição.")
                return redirect(request.path)
            payload = {name: request.POST.get(name, "") for name in ("action", "name", "description", "role_id", "user_id", "expires_at")}
            payload["permissions"] = request.POST.getlist("permissions")
            result = preview(request.user, payload, self.organization)
            return render(request, "access_control/preview.html", {"preview": result, "organization": self.organization})
        except (ValidationError, signing.BadSignature, KeyError, ValueError, TypeError, Role.DoesNotExist, get_user_model().DoesNotExist) as exc:
            messages.error(request, "; ".join(exc.messages) if isinstance(exc, ValidationError) else "Prévia inválida ou expirada. Confira os dados e tente novamente.")
            return redirect(request.path)


class OrganizationSelector(View):
    def get(self, request):
        if not request.user.is_authenticated:
            raise Http404()
        organizations = Organization.objects.filter(active=True).order_by("name", "pk")
        if not request.user.is_superuser:
            organizations = organizations.filter(memberships__user=request.user)
        q = request.GET.get("q", "")[:100]
        return render(request, "access_control/organizations.html", {"page_obj": Paginator(organizations.filter(name__icontains=q), 25).get_page(request.GET.get("page")), "q": q})

    def post(self, request):
        if not request.user.is_authenticated:
            raise Http404()
        organizations = Organization.objects.filter(active=True)
        if not request.user.is_superuser:
            organizations = organizations.filter(memberships__user=request.user)
        try:
            organization = get_object_or_404(organizations, pk=request.POST.get("organization"))
        except (ValueError, ValidationError):
            raise Http404()
        request.session["active_organization"] = str(organization.pk)
        return redirect("orgs:overview")
