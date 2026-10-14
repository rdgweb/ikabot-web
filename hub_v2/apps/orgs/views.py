"""Minha organização (N-106, stage 4): what a customer sets for their own organization."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.http import Http404
from django.shortcuts import redirect
from django.views.generic import TemplateView

from .models import Membership, OrganizationPreferences
from .services import preferences_of


def membership_of(request):
    organization = getattr(request, "organization", None)
    if organization is None:
        return None
    return request.user.memberships.filter(organization=organization).first()


def can_change(request) -> bool:
    membership = membership_of(request)
    return bool(membership and membership.role in (Membership.OWNER, Membership.OPERATOR))


def bot_username() -> str:
    """The platform bot's @name, for the link that opens the chat with the code ready."""
    cached = cache.get("telegram:bot-username")
    if cached is not None:
        return cached
    from apps.telegram.services.bot_api import get_bot_info

    info = get_bot_info() or {}
    username = str(info.get("username") or "")
    cache.set("telegram:bot-username", username, timeout=3600 if username else 300)
    return username


class OrganizationTelegramView(LoginRequiredMixin, TemplateView):
    """GET/POST /organizacao/telegram/ — this organization's chat and its notices."""

    template_name = "orgs/telegram.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and getattr(request, "organization", None) is None:
            raise Http404()
        return super().dispatch(request, *args, **kwargs)

    def _config(self):
        from apps.telegram.services.config import organization_config

        return organization_config(self.request.organization)

    def get_context_data(self, **kwargs):
        from apps.telegram.forms import TelegramGlobalNotificationForm
        from apps.telegram.services.config import platform_bot

        ctx = super().get_context_data(**kwargs)
        config = self._config()
        code = self.request.session.pop("telegram-link-code", "")
        username = bot_username() if platform_bot().is_active else ""
        ctx.update({
            "config": config,
            "bot_ready": platform_bot().is_active,
            "bot_username": username,
            "code": code,
            "deep_link": f"https://t.me/{username}?start={code}" if code and username else "",
            "form": TelegramGlobalNotificationForm(instance=config),
            "can_change": can_change(self.request),
            "preferences": preferences_of(self.request.organization),
            "captcha_choices": OrganizationPreferences.CAPTCHA_TELEGRAM_CHOICES,
        })
        return ctx

    def post(self, request):
        if not can_change(request):
            raise Http404()
        from apps.telegram.forms import TelegramGlobalNotificationForm
        from apps.telegram.services.linking import generate_global_link_code, unlink_global

        action = request.POST.get("action")
        if action == "link":
            request.session["telegram-link-code"] = generate_global_link_code(request.organization)
        elif action == "unlink":
            unlink_global(request.organization)
            messages.success(request, "Chat desvinculado.")
        elif action == "captcha":
            choice = request.POST.get("captcha_telegram", "")
            if choice in dict(OrganizationPreferences.CAPTCHA_TELEGRAM_CHOICES):
                preferences = preferences_of(request.organization)
                preferences.captcha_telegram = choice
                preferences.save(update_fields=["captcha_telegram", "updated_at"])
                messages.success(request, "Escolha do captcha salva.")
        elif action == "notices":
            form = TelegramGlobalNotificationForm(request.POST, instance=self._config())
            if form.is_valid():
                form.save()
                messages.success(request, "Avisos salvos.")
        return redirect("orgs:telegram")
