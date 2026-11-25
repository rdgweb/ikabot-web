"""Serialized, previewed changes with a delegation ceiling and stale-preview detection."""
import hashlib
import json
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from apps.orgs.models import Membership, Organization
from apps.settings_app.models import AppSetting
from .catalog import CATALOG, OWNER
from .models import AccessEvent, AccessMode, Grant, Role
from .policy import allowed, permissions, sources


def key(organization):
    return str(organization.pk) if organization else "platform"


def lock():
    AppSetting.objects.get_or_create(pk="access_management_lock")
    AppSetting.objects.select_for_update().get(pk="access_management_lock")


def fingerprint():
    """Small aggregate database revision, not a scan of people or memberships."""
    from django.db.models import Count, Max
    state = [model.objects.aggregate(count=Count("pk"), changed=Max("updated_at")) for model in (Role, Grant, AccessEvent)]
    return hashlib.sha256(json.dumps(state, default=str, sort_keys=True).encode()).hexdigest()


def event(actor, action, target, organization, before, after):
    AccessEvent.objects.create(actor=actor, actor_label=actor.username, action=action,
        target_label=str(target), organization=organization, before=before, after=after)
    from apps.audit.services import record
    record("access.changed", user=actor, target_label=str(target), organization=organization,
           details={"action": action, "before": before, "after": after})


def _owner_exists(organization, excluding):
    managed = AccessMode.objects.filter(scope_key=str(organization.pk)).values_list("user_id", flat=True)
    if Membership.objects.filter(organization=organization, role="owner", user__is_active=True).exclude(user=excluding).exclude(user_id__in=managed).exists():
        return True
    return Grant.objects.filter(organization=organization, role__system_key="owner", user__is_active=True,
        revoked_at__isnull=True, expires_at__isnull=True).exclude(user=excluding).exists()


def _is_owner(user, organization):
    managed = AccessMode.objects.filter(user=user, scope_key=str(organization.pk)).exists()
    return (not managed and Membership.objects.filter(user=user, organization=organization, role="owner").exists()) or Grant.objects.filter(
        user=user, organization=organization, role__system_key="owner", revoked_at__isnull=True, expires_at__isnull=True).exists()


def prepare(actor, payload, organization=None):
    scope = "organization" if organization else "platform"
    if not allowed(actor, f"{scope}.access.manage", organization):
        raise ValidationError("Você não pode gerenciar acessos neste escopo.")
    action = payload.get("action")
    if action == "role":
        role = Role.objects.get(pk=payload["role_id"]) if payload.get("role_id") else None
        if role and (role.system_key or role.scope != scope or role.organization_id != getattr(organization, "pk", None)):
            raise ValidationError("Este papel é protegido ou pertence a outro escopo.")
        selected = set(payload.get("permissions", []))
        if not selected or not selected <= {p for p in CATALOG if p.startswith(scope + ".")}:
            raise ValidationError("Selecione permissões válidas para este escopo.")
        # Editing a role already in use is also a grant of every resulting permission.
        if not selected <= permissions(actor, organization):
            raise ValidationError("Você só pode delegar permissões que possui neste escopo.")
        if role and not set(role.permissions) <= permissions(actor, organization):
            raise ValidationError("Você não pode editar um papel acima do seu próprio acesso.")
        name = str(payload.get("name", "")).strip()
        if not name or len(name) > 100:
            raise ValidationError("Informe um nome de até 100 caracteres.")
        return {"action": action, "name": name, "permissions": sorted(selected),
                "description": str(payload.get("description", ""))[:500], "role_id": str(role.pk) if role else ""}
    if action not in ("grant", "revoke"):
        raise ValidationError("Ação inválida. Use a prévia antes de confirmar.")
    user = get_user_model().objects.get(pk=payload["user_id"])
    if user.is_superuser:
        raise ValidationError("O administrador principal é protegido.")
    if not actor.is_superuser and not AccessMode.objects.filter(user=user, scope_key=key(organization)).exists():
        existing = {p for p in permissions(user, organization) if p.startswith(scope + ".")}
        if not existing <= permissions(actor, organization):
            raise ValidationError("A transição do acesso legado desta pessoa exige um responsável com todas as permissões atuais dela.")
    if organization and not Membership.objects.filter(user=user, organization=organization).exists():
        raise ValidationError("A pessoa precisa ser membro desta organização.")
    role = Role.objects.get(pk=payload["role_id"])
    if role.scope != scope or role.organization_id not in (None, getattr(organization, "pk", None)):
        raise ValidationError("O papel não pertence a este escopo.")
    if not set(role.permissions) <= permissions(actor, organization):
        raise ValidationError("Você não pode conceder ou revogar um papel acima do seu acesso.")
    expires = None
    if payload.get("expires_at"):
        expires = parse_datetime(payload["expires_at"])
        if expires and timezone.is_naive(expires):
            expires = timezone.make_aware(expires)
        if expires is None or expires <= timezone.now():
            raise ValidationError("A expiração deve ser uma data futura válida.")
    if role.system_key == "owner" and expires:
        raise ValidationError("O papel Dono não pode expirar automaticamente.")
    if action == "revoke" and not Grant.objects.filter(user=user, role=role, scope_key=key(organization), revoked_at__isnull=True).exists():
        raise ValidationError("Esta concessão já não está ativa.")
    if organization and _is_owner(user, organization) and not _owner_exists(organization, user):
        # The first custom grant replaces legacy rights. Only a permanent owner grant preserves ownership.
        existing_owner = Grant.objects.filter(user=user, organization=organization, role__system_key="owner", revoked_at__isnull=True).exists()
        removes_owner = (action == "revoke" and role.system_key == "owner") or (action == "grant" and role.system_key != "owner" and not existing_owner)
        if removes_owner:
            raise ValidationError("A organização precisa manter pelo menos um dono ativo.")
    return {"action": action, "user_id": user.pk, "role_id": str(role.pk),
            "expires_at": expires.isoformat() if expires else ""}


def preview(actor, payload, organization=None):
    normalized = prepare(actor, payload, organization)
    before, after = set(), set()
    if normalized["action"] == "role":
        if normalized["role_id"]:
            before = set(Role.objects.get(pk=normalized["role_id"]).permissions)
        after = set(normalized["permissions"])
    else:
        user = get_user_model().objects.get(pk=normalized["user_id"])
        before = permissions(user, organization)
        for source in sources(user, organization):
            grant = source.get("grant")
            if organization and not grant and source["name"] == "Administrador da plataforma (legado)":
                after.update(source["permissions"])
            if grant and grant.scope_key == key(organization) and str(grant.role_id) != normalized["role_id"]:
                after.update(source["permissions"])
            elif organization and grant and grant.scope_key == "platform":
                after.update(source["permissions"])
        if normalized["action"] == "grant":
            after.update(Role.objects.get(pk=normalized["role_id"]).permissions)
        # Organization changes leave platform compatibility permissions intact.
        if organization:
            after.update(p for p in before if p.startswith("platform."))
    baseline = sorted(permissions(get_user_model().objects.get(pk=normalized["user_id"]), organization)) if normalized.get("user_id") else None
    token = signing.dumps({"actor": actor.pk, "scope": key(organization), "payload": normalized,
                           "baseline": baseline, "revision": fingerprint()}, salt="access-preview", compress=True)
    role = Role.objects.get(pk=normalized["role_id"]) if normalized.get("role_id") else None
    target = get_user_model().objects.get(pk=normalized["user_id"]).username if normalized.get("user_id") else normalized["name"]
    return {"token": token, "added": sorted(after - before), "removed": sorted(before - after), "payload": normalized,
            "added_labels": [CATALOG[p] for p in sorted(after - before)], "removed_labels": [CATALOG[p] for p in sorted(before - after)],
            "target": target, "role_name": role.name if role else normalized.get("name", ""),
            "action_label": {"role": "Salvar papel", "grant": "Conceder papel", "revoke": "Revogar papel"}[normalized["action"]],
            "affected": role.grants.filter(revoked_at__isnull=True).count() if role and normalized["action"] == "role" else 1}


@transaction.atomic
def confirm(actor, token, organization=None):
    from .policy import _cache
    cache = _cache.get()
    if cache is not None:
        cache.clear()
    lock()
    data = signing.loads(token, salt="access-preview", max_age=900)
    if data["actor"] != actor.pk or data["scope"] != key(organization) or data["revision"] != fingerprint():
        raise ValidationError("A prévia mudou ou já foi utilizada. Gere outra antes de confirmar.")
    payload = prepare(actor, data["payload"], organization)
    if payload.get("user_id") and data["baseline"] != sorted(permissions(get_user_model().objects.get(pk=payload["user_id"]), organization)):
        raise ValidationError("O acesso da pessoa mudou. Gere outra prévia.")
    if payload["action"] == "role":
        role = Role.objects.get(pk=payload["role_id"]) if payload["role_id"] else Role(scope="organization" if organization else "platform", organization=organization)
        before = {"name": role.name, "permissions": role.permissions}
        role.name, role.description, role.permissions = payload["name"], payload["description"], payload["permissions"]
        role.version += 1
        role.full_clean()
        role.save()
        event(actor, "role.saved", role, organization, before, payload)
    else:
        user = get_user_model().objects.get(pk=payload["user_id"])
        before = {"permissions": sorted(permissions(user, organization))}
        AccessMode.objects.get_or_create(user=user, scope_key=key(organization))
        grant, _ = Grant.objects.get_or_create(user=user, role_id=payload["role_id"], scope_key=key(organization),
            defaults={"organization": organization, "created_by": actor})
        grant.revoked_at = timezone.now() if payload["action"] == "revoke" else None
        grant.expires_at = parse_datetime(payload["expires_at"]) if payload["expires_at"] else None
        grant.created_by = actor
        grant.save()
        if cache is not None:
            cache.clear()
        event(actor, payload["action"], user, organization, before,
              {"permissions": sorted(permissions(user, organization)), "role_id": payload["role_id"], "expires_at": payload["expires_at"]})


def protect_owner_identity(user):
    """Called under the same IAM lock by identity deletion/deactivation forms."""
    if user.is_superuser:
        raise ValidationError("O administrador principal é protegido.")
    if not user.is_active:
        return
    for membership in user.memberships.select_related("organization"):
        if _is_owner(user, membership.organization) and not _owner_exists(membership.organization, user):
            raise ValidationError("Esta pessoa é o último dono ativo de uma organização. Transfira a responsabilidade antes de desativar ou excluir.")
