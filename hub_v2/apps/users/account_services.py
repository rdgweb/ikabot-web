"""Personal data and email identity; no organization privileges are changed here."""
import hashlib
import io
import logging
import secrets
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from apps.settings_app.models import AppSetting
from .models import EmailChallenge, UserProfile

logger = logging.getLogger(__name__)
EMAIL_INTERVAL = timedelta(seconds=60)


def profile_for(user):
    return UserProfile.objects.get_or_create(user=user)[0]


def email_lock():
    AppSetting.objects.get_or_create(pk="account_email_lock")
    AppSetting.objects.select_for_update().get(pk="account_email_lock")


def normalized_email(value):
    email = (value or "").strip().lower()
    validate_email(email)
    if len(email) > 254:
        raise ValidationError("O e-mail deve ter até 254 caracteres.")
    return email


def ensure_email_available(email, user=None):
    query = get_user_model().objects.filter(email__iexact=email)
    if user:
        query = query.exclude(pk=user.pk)
    if query.exists():
        raise ValidationError("Este e-mail já é usado por outra conta.")


def check_email_interval(profile):
    if profile.last_email_sent_at and profile.last_email_sent_at > timezone.now() - EMAIL_INTERVAL:
        raise ValidationError("Aguarde um minuto antes de pedir outro e-mail.")


def email_rate_limit(request):
    from django.core.cache import cache
    from apps.audit.services import client_ip
    subject = str(request.user.pk) if request.user.is_authenticated else "ip:"+str(client_ip(request))
    key = "account-email-hour:"+digest(subject)
    cache.add(key, 0, timeout=3600)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=3600)
        count = 1
    if count > 10:
        raise ValidationError("Muitos pedidos de e-mail. Aguarde antes de tentar novamente.")


def sensitive_rate_limit(user):
    from django.core.cache import cache
    key = "account-sensitive:"+str(user.pk)
    cache.add(key, 0, timeout=300)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=300)
        count = 1
    if count > 10:
        raise ValidationError("Muitas tentativas. Aguarde cinco minutos antes de tentar novamente.")


def mark_verified(user):
    profile = profile_for(user)
    profile.verified_email = user.email.strip().lower()
    profile.email_verified_at = timezone.now()
    profile.signup_organization = ""
    profile.signup_pending = False
    profile.save(update_fields=["verified_email", "email_verified_at", "signup_organization", "signup_pending", "updated_at"])
    return profile


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def account_url(request, path):
    base = (getattr(settings, "SITE_URL", "") or settings.HUB_PUBLIC_URL or "").rstrip("/")
    return base + path if base else request.build_absolute_uri(path)


def normalize_avatar(upload):
    from PIL import Image, ImageOps, UnidentifiedImageError
    if upload.size > 2 * 1024 * 1024:
        raise ValidationError("Escolha uma foto de até 2 MB.")
    data = upload.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValidationError("Escolha uma foto de até 2 MB.")
    try:
        with Image.open(io.BytesIO(data), formats=["JPEG", "PNG", "WEBP"]) as image:
            if image.width * image.height > 16_000_000 or getattr(image, "is_animated", False):
                raise ValidationError("Escolha uma imagem estática com até 16 milhões de pixels.")
            image.verify()
        with Image.open(io.BytesIO(data), formats=["JPEG", "PNG", "WEBP"]) as image:
            image = ImageOps.fit(ImageOps.exif_transpose(image), (256, 256)).convert("RGBA")
            clean = Image.new("RGB", (256, 256), "white")
            clean.paste(image, mask=image.getchannel("A"))
            output = io.BytesIO()
            clean.save(output, "JPEG", quality=85, optimize=True)
            result = output.getvalue()
            if len(result) > 60_000:
                raise ValidationError("Não foi possível reduzir esta imagem. Escolha outra foto.")
            return result
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValidationError("Use uma foto válida em JPG, PNG ou WebP.") from exc


def request_email_confirmation(request, target=None, password=None):
    if not settings.EMAIL_READY:
        raise ValidationError("O envio de e-mail está indisponível. Peça ao administrador para configurar o serviço.")
    email_rate_limit(request)
    explicit_target = target
    user = get_user_model().objects.get(pk=request.user.pk)
    target = normalized_email(target if target is not None else user.email)
    purpose = "change" if target.casefold() != user.email.strip().casefold() else "verify"
    if purpose == "change" and not user.check_password(password or ""):
        raise ValidationError("Confirme sua senha atual para solicitar a troca de e-mail.")
    token = secrets.token_urlsafe(32)
    with transaction.atomic():
        email_lock()
        user = get_user_model().objects.select_for_update().get(pk=user.pk)
        target = normalized_email(explicit_target if explicit_target is not None else user.email)
        purpose = "change" if target.casefold() != user.email.strip().casefold() else "verify"
        if not user.is_active or (purpose == "change" and not user.check_password(password or "")):
            raise ValidationError("Confira a senha atual e tente novamente.")
        ensure_email_available(target, user)
        profile = profile_for(user)
        check_email_interval(profile)
        challenge, _ = EmailChallenge.objects.update_or_create(user=user, defaults={
            "target_email": target, "source_email": user.email, "password_digest": digest(user.password),
            "token_digest": digest(token), "purpose": purpose, "expires_at": timezone.now()+timedelta(hours=24),
            "used_at": None, "delivery": "pending"})
        profile.last_email_sent_at = timezone.now()
        profile.save(update_fields=["last_email_sent_at", "updated_at"])
    link = account_url(request, f"/conta/email/confirmar/{token}/")
    try:
        delivered = send_mail("Confirme seu e-mail no IkaHub", f"Olá, {user.get_username()}.\n\nPara confirmar o endereço {target}, abra o link e confirme no IkaHub:\n{link}\n\nO link vale por 24 horas, só funciona uma vez e exige acesso à sua conta. Se não pediu isso, ignore esta mensagem.", None, [target])
        if delivered != 1:
            raise RuntimeError("email_not_delivered")
    except Exception as exc:
        EmailChallenge.objects.filter(pk=challenge.pk, token_digest=digest(token)).update(delivery="failed")
        logger.warning("Account email delivery failed (%s)", type(exc).__name__)
        from apps.audit.services import record
        record("user.email_delivery_failed", user=user, target=user, details={"purpose": purpose})
        raise ValidationError("Não foi possível enviar o e-mail. O endereço atual foi preservado; tente novamente em um minuto.") from exc
    EmailChallenge.objects.filter(pk=challenge.pk, token_digest=digest(token)).update(delivery="sent")
    from apps.audit.services import record
    record("user.email_requested", user=user, target=user, details={"purpose": purpose})
    return challenge


def valid_challenge(user, token):
    if not token or len(token) > 100:
        return None
    return EmailChallenge.objects.filter(user=user, token_digest=digest(token), used_at__isnull=True, delivery="sent",
        expires_at__gt=timezone.now(), source_email=user.email, password_digest=digest(user.password)).first()


@transaction.atomic
def confirm_email(request, token):
    email_lock()
    user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
    challenge = valid_challenge(user, token) if user.is_active else None
    if challenge is None:
        raise ValidationError("Este link expirou, já foi utilizado ou não pertence à sua conta. Solicite outro em Minha conta.")
    ensure_email_available(challenge.target_email, user)
    user.email = challenge.target_email
    user.save(update_fields=["email"])
    mark_verified(user)
    challenge.used_at = timezone.now()
    challenge.save(update_fields=["used_at", "updated_at"])
    request.user.email = user.email
    from apps.audit.services import record
    record("user.email_changed" if challenge.purpose == "change" else "user.email_verified", user=user, target=user)
    return challenge.purpose


def resend_signup(request, email):
    """The same generic response for unknown, active, throttled and pending accounts."""
    from apps.orgs.services import signup_token
    if not settings.EMAIL_READY:
        return
    try:
        email_rate_limit(request)
        email = normalized_email(email)
        with transaction.atomic():
            email_lock()
            user = get_user_model().objects.select_for_update().filter(email__iexact=email, is_active=False, memberships__isnull=True).first()
            if not user:
                return
            profile = profile_for(user)
            if not profile.signup_pending:
                return
            check_email_interval(profile)
            profile.email_generation += 1
            profile.last_email_sent_at = timezone.now()
            profile.save(update_fields=["email_generation", "last_email_sent_at", "updated_at"])
            token = signup_token(user, profile.signup_organization or user.username, profile.signup_language)
        link = account_url(request, f"/conta/confirmar/{token}/")
        result = send_mail("Confirme seu cadastro no IkaHub", f"Para confirmar o cadastro, abra:\n{link}\n\nO link vale por 48 horas. Se não pediu este cadastro, ignore a mensagem.", None, [user.email])
        from apps.audit.services import record
        record("user.email_requested" if result == 1 else "user.email_delivery_failed", user=user, target=user, details={"purpose": "signup"})
    except ValidationError:
        return
    except Exception as exc:
        logger.warning("Signup confirmation delivery failed (%s)", type(exc).__name__)
