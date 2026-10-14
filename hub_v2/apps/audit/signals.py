"""What a person changes on the screens becomes an audit event (N-120).

Only changes made inside a person's web request count: the agent's heartbeats, automatic jobs and
commands are not "someone did something". Secrets never go in: proxies are P<id>, settings only
their key, passwords only the fact that they changed.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver

from .services import ACTIONS, acting_user, record

NOISE_FIELDS = {"last_login", "updated_at", "agent_last_seen_at", "last_seen_at"}
TRACKED = {
    ("accounts", "Account"): "account",
    ("accounts", "GameAccount"): "gameaccount",
    ("accounts", "Node"): "node",
    ("accounts", "DockerHost"): "dockerhost",
    ("proxy", "ProxyProfile"): "proxyprofile",
    ("profiles", "Preset"): "preset",
    ("jobs", "Job"): "job",
    ("jobs", "Workflow"): "workflow",
}


def label(instance) -> str:
    name = type(instance).__name__
    if name == "ProxyProfile":
        return f"P{instance.pk}"
    if name == "GameAccount":
        return f"{instance.name or '-'} ({instance.server_id})"
    if name == "Job":
        from core.actions.registry import get_action_info

        info = get_action_info(instance.action_code) or {}
        account = getattr(instance, "game_account", None)
        return f"{info.get('name') or f'ação {instance.action_code}'}" + (f" · {account.name or account.server_id}" if account else "")
    for attribute in ("label", "name", "username"):
        value = getattr(instance, attribute, "")
        if value:
            return str(value)
    return str(instance)


def _prefix(sender):
    return TRACKED.get((sender._meta.app_label, sender.__name__))


@receiver(post_save)
def model_saved(sender, instance, created, raw=False, update_fields=None, **kwargs):
    prefix = _prefix(sender)
    if prefix is None or raw or acting_user() is None:
        return
    if update_fields and set(update_fields) <= NOISE_FIELDS:
        return
    action = f"{prefix}.created" if created else f"{prefix}.updated"
    if action in ACTIONS:
        record(action, target=instance, target_label=label(instance),
               details={"campos": sorted(update_fields)} if update_fields else None)


@receiver(post_delete)
def model_deleted(sender, instance, **kwargs):
    prefix = _prefix(sender)
    if prefix is None or acting_user() is None:
        return
    action = f"{prefix}.deleted"
    if action in ACTIONS:
        record(action, target=instance, target_label=label(instance))


# ── access ──────────────────────────────────────────────────────────────────────

@receiver(user_logged_in)
def logged_in(sender, request, user, **kwargs):
    record("login", user=user, target=user, target_label=user.get_username())


@receiver(user_logged_out)
def logged_out(sender, request, user, **kwargs):
    if user is not None:
        record("logout", user=user, target=user, target_label=user.get_username())


@receiver(user_login_failed)
def login_failed(sender, credentials, request=None, **kwargs):
    username = str((credentials or {}).get("username", ""))[:150]   # Django already hides the password
    record("login_failed", actor_kind="anonymous", actor_label=username, target_label=username, result="failed")


# ── settings (key only, never the value) ─────────────────────────────────────────

@receiver(post_save, sender="settings_app.AppSetting")
def setting_saved(sender, instance, raw=False, **kwargs):
    if raw or acting_user() is None or instance.key.startswith(("tour_done:", "audit:")):
        return
    record("setting.changed", target=instance, target_label=instance.key)


@receiver(post_save, sender="telegram.TelegramBotConfig")
def telegram_saved(sender, instance, raw=False, **kwargs):
    if not raw and acting_user() is not None:
        record("telegram.changed", target=instance, target_label="Bot do Telegram")


@receiver(post_save, sender="captcha.CaptchaConfig")
def captcha_saved(sender, instance, raw=False, **kwargs):
    if not raw and acting_user() is not None:
        record("captcha.changed", target=instance, target_label="Captcha")


# ── users ───────────────────────────────────────────────────────────────────────

User = get_user_model()


@receiver(pre_save, sender=User)
def remember_password(sender, instance, raw=False, **kwargs):
    if raw or not instance.pk:
        return
    instance._audit_old_password = User.objects.filter(pk=instance.pk).values_list("password", flat=True).first()


@receiver(post_save, sender=User)
def user_saved(sender, instance, created, raw=False, update_fields=None, **kwargs):
    if raw or acting_user() is None:
        return
    if update_fields and set(update_fields) <= NOISE_FIELDS:
        return
    if created:
        record("user.created", target=instance, target_label=instance.get_username())
    elif getattr(instance, "_audit_old_password", None) not in (None, instance.password):
        record("user.password", target=instance, target_label=instance.get_username())
    else:
        record("user.updated", target=instance, target_label=instance.get_username())


@receiver(post_delete, sender=User)
def user_deleted(sender, instance, **kwargs):
    if acting_user() is not None:
        record("user.deleted", target=instance, target_label=instance.get_username())


@receiver(m2m_changed, sender=User.groups.through)
def user_groups(sender, instance, action, reverse=False, **kwargs):
    if reverse or action not in ("post_add", "post_remove") or acting_user() is None:
        return
    record("membership.changed", target=instance, target_label=instance.get_username(),
           details={"grupos": sorted(instance.groups.values_list("name", flat=True))})
