"""
Views para o app Captcha — configuracao, resolucao manual, historico.
"""

import logging
from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from .forms import CaptchaConfigForm, CaptchaSolveForm
from .models import CaptchaConfig, CaptchaChallenge

logger = logging.getLogger(__name__)


class CaptchaConfigView(LoginRequiredMixin, TemplateView):
    """
    Main captcha page: configuration + pending challenges + history.
    """

    template_name = "captcha/config.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        config, _ = CaptchaConfig.objects.get_or_create(pk=1)
        ctx["config"] = config
        ctx["config_form"] = CaptchaConfigForm(instance=config)

        # Expire old pending challenges
        CaptchaChallenge.objects.filter(
            status__in=["pending", "solving"],
            expires_at__lt=timezone.now(),
        ).update(status="expired")

        # Pending challenges (for manual resolution)
        ctx["pending"] = CaptchaChallenge.objects.filter(
            status__in=["pending", "solving"],
        ).select_related("game_account", "node")[:20]

        # Stats
        now = timezone.now()
        last_24h = now - timedelta(hours=24)
        challenges_24h = CaptchaChallenge.objects.filter(created_at__gte=last_24h)
        ctx["stats"] = {
            "total_24h": challenges_24h.count(),
            "solved_24h": challenges_24h.filter(status="solved").count(),
            "failed_24h": challenges_24h.filter(status="failed").count(),
            "expired_24h": challenges_24h.filter(status="expired").count(),
            "pending_now": ctx["pending"].count(),
        }

        # Recent history
        ctx["history"] = CaptchaChallenge.objects.exclude(
            status__in=["pending", "solving"],
        ).select_related("game_account", "node")[:50]

        # Method descriptions for the UI
        ctx["method_info"] = {
            "auto": {
                "icon": "bi-cpu",
                "title": "Automatico (ikabotapi)",
                "desc": "O servico ikabotapi resolve automaticamente usando IA. Mais rapido, sem intervencao humana.",
                "color": "var(--ik-sea)",
            },
            "manual": {
                "icon": "bi-person",
                "title": "Manual (interface web)",
                "desc": "Voce resolve na interface quando o agent encontra captcha. Mais confiavel, requer atencao.",
                "color": "var(--ik-gold)",
            },
            "telegram": {
                "icon": "bi-telegram",
                "title": "Assistido (Telegram)",
                "desc": "Imagem enviada no Telegram, voce responde pelo chat. Pratico para quem esta no celular.",
                "color": "var(--ik-good)",
            },
        }

        return ctx


class CaptchaSaveConfigView(LoginRequiredMixin, View):
    """POST: save captcha configuration."""

    def post(self, request):
        config, _ = CaptchaConfig.objects.get_or_create(pk=1)
        form = CaptchaConfigForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            config.refresh_from_db()

        ctx = {
            "config": config,
            "config_form": CaptchaConfigForm(instance=config),
        }
        html = render_to_string(
            "captcha/partials/config_card.html", ctx, request=request,
        )
        response = HttpResponse(html)
        response["HX-Trigger"] = "showToast"
        return response


class CaptchaSolveView(LoginRequiredMixin, View):
    """
    GET: show solve form for a pending challenge.
    POST: submit solution.
    """

    def get(self, request, pk):
        challenge = get_object_or_404(CaptchaChallenge, pk=pk)
        if challenge.is_expired:
            challenge.mark_expired()

        form = CaptchaSolveForm()
        ctx = {"challenge": challenge, "form": form}
        html = render_to_string(
            "captcha/partials/solve_modal.html", ctx, request=request,
        )
        return HttpResponse(html)

    def post(self, request, pk):
        challenge = get_object_or_404(CaptchaChallenge, pk=pk)

        if challenge.is_expired:
            challenge.mark_expired()
            return HttpResponse(
                '<div class="text-center p-4 text-[var(--ik-bad)]">'
                '<i class="bi bi-clock-history text-2xl"></i>'
                '<p class="mt-2 text-sm font-semibold">Captcha expirado</p>'
                '</div>'
            )

        form = CaptchaSolveForm(request.POST)
        if form.is_valid():
            challenge.mark_solved(
                solution=form.cleaned_data["solution"],
                method="manual",
            )
            html = render_to_string(
                "captcha/partials/solve_success.html",
                {"challenge": challenge},
                request=request,
            )
            response = HttpResponse(html)
            response["HX-Trigger"] = "captchaSolved"
            return response

        ctx = {"challenge": challenge, "form": form}
        html = render_to_string(
            "captcha/partials/solve_modal.html", ctx, request=request,
        )
        return HttpResponse(html)


class CaptchaSkipView(LoginRequiredMixin, View):
    """POST: skip/fail a captcha challenge."""

    def post(self, request, pk):
        challenge = get_object_or_404(CaptchaChallenge, pk=pk)
        challenge.mark_failed(error="Ignorado pelo usuario")
        response = HttpResponse("")
        response["HX-Trigger"] = "captchaSolved"
        return response


class CaptchaPendingView(LoginRequiredMixin, View):
    """GET: refresh pending captchas panel (HTMX polling)."""

    def get(self, request):
        # Expire old ones
        CaptchaChallenge.objects.filter(
            status__in=["pending", "solving"],
            expires_at__lt=timezone.now(),
        ).update(status="expired")

        pending = CaptchaChallenge.objects.filter(
            status__in=["pending", "solving"],
        ).select_related("game_account", "node")[:20]

        html = render_to_string(
            "captcha/partials/pending_list.html",
            {"pending": pending},
            request=request,
        )
        return HttpResponse(html)
