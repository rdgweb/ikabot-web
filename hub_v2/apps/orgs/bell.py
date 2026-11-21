"""N-207: the bell at the top — what happened lately for this organization: the notices sent (the same ones the
Telegram receives), captchas waiting for someone, and jobs that failed today. Seen: everything up to the last time the
person opened the bell."""
from __future__ import annotations

from datetime import datetime, timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views import View

LIMIT = 12


def _staff(user) -> bool:
    return bool(user.is_staff or user.is_superuser)


def _seen_key(user) -> str:
    return f"bell_seen:{user.pk}"


def _seen(user) -> datetime:
    from apps.settings_app.utils import get_setting

    stamp = parse_datetime(get_setting(_seen_key(user), "") or "")
    return stamp or (timezone.now() - timedelta(days=1))


def items(limit: int = LIMIT, *, platform: bool = False) -> list[dict]:
    """The latest things, newest first (the managers already keep to the organization of whoever is logged in)."""
    from apps.captcha.models import CaptchaChallenge
    from apps.jobs.models import Job
    from apps.telegram.models import MessageAudit
    from core.templatetags.hub_tags import action_name

    out = []
    notices = MessageAudit.objects.all() if platform else MessageAudit.objects.filter(game_account__isnull=False)
    for m in notices.order_by("-created_at")[:limit]:   # rows without an account are the platform's (system administrator)
        title = (m.title or m.body or "Aviso").strip().splitlines()[0][:90]
        out.append({"when": m.created_at, "icon": "bi-send", "tone": "bad" if m.status == "failed" else "",
                    "title": title, "note": "aviso" + (" · não entregue" if m.status == "failed" else ""), "url": ""})
    for c in CaptchaChallenge.objects.filter(status__in=("pending", "solving")).order_by("-created_at")[:5]:
        out.append({"when": c.created_at, "icon": "bi-puzzle", "tone": "warn", "title": "Captcha esperando",
                    "note": c.get_captcha_type_display(), "url": "/captcha/pending/"})
    since = timezone.now() - timedelta(hours=24)
    for j in Job.objects.filter(status="failed", finished_at__gte=since).select_related("game_account").order_by("-finished_at")[:5]:
        out.append({"when": j.finished_at, "icon": "bi-x-octagon", "tone": "bad", "title": f"Falhou: {action_name(j.action_code)}",
                    "note": getattr(j.game_account, "name", "") or "job", "url": f"/jobs/{j.pk}/"})
    out.sort(key=lambda i: i["when"], reverse=True)
    return out[:limit]


class BellCountView(LoginRequiredMixin, View):
    """The dot on the bell: how many are new (polled every minute)."""

    def get(self, request):
        seen = _seen(request.user)
        try:
            new = sum(1 for i in items(platform=_staff(request.user)) if i["when"] and i["when"] > seen)
        except Exception:  # noqa: BLE001 — the bell never breaks a page
            new = 0
        return render(request, "orgs/partials/bell_count.html", {"new": new})


class BellListView(LoginRequiredMixin, View):
    """Opening the bell: the list, and everything in it counts as seen."""

    def get(self, request):
        from apps.settings_app.utils import set_setting

        seen = _seen(request.user)
        rows = items(platform=_staff(request.user))
        for row in rows:
            row["new"] = bool(row["when"] and row["when"] > seen)
        set_setting(_seen_key(request.user), timezone.now().isoformat())
        return render(request, "orgs/partials/bell_list.html", {"rows": rows})
