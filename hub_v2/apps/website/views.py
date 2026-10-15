"""Public site (N-119): sales page, pricing, FAQ, legal pages and the invitation request, in pt and en."""
import json
import logging
from datetime import timedelta

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.orgs.services import TERMS_VERSION, signup_available

from . import content as C
from .models import InviteRequest

logger = logging.getLogger(__name__)

REQUESTS_PER_HOUR = 5


def site_url(request) -> str:
    return settings.SITE_URL or f"{request.scheme}://{request.get_host()}"


def preferred_language(request) -> str:
    accept = (request.META.get("HTTP_ACCEPT_LANGUAGE") or "").lower()
    return "pt" if accept.startswith("pt") else "en"


def home_for(request):
    """Where '/' sends someone who is not logged in."""
    return HttpResponseRedirect(C.path_of(preferred_language(request), "home"))


def _context(request, lang: str, page: str, **extra) -> dict:
    base = site_url(request)
    title, description = C.META[lang][page]
    alternates = [(C.HTML_LANG[code], base + C.path_of(code, page)) for code in C.LANGS]
    other = "en" if lang == "pt" else "pt"
    return {
        "lang": lang, "html_lang": C.HTML_LANG[lang], "page": page,
        "title": title, "description": description,
        "canonical": base + C.path_of(lang, page),
        "alternates": alternates, "x_default": base + C.path_of(C.X_DEFAULT, page),
        "other_lang_url": C.path_of(other, page),
        "og_image": base + "/static/website/og.png",
        "indexable": settings.SITE_INDEXABLE,
        "ui": C.UI[lang], "links": {key: C.path_of(lang, key) for key in C.PAGES},
        "open_signup": signup_available(),
        "cta": ({"label": C.UI[lang]["start"], "href": C.path_of(lang, "signup"), "icon": "bi-rocket-takeoff"} if signup_available()
                else {"label": C.UI[lang]["invite"], "href": C.path_of(lang, "contact"), "icon": "bi-envelope-paper"}),
        "legal": settings.SITE_LEGAL, "terms_version": TERMS_VERSION,
        "umami": bool(settings.UMAMI_URL and settings.UMAMI_WEBSITE_ID), "umami_id": settings.UMAMI_WEBSITE_ID,
        "structured": json.dumps(_structured(base, lang, page), ensure_ascii=False).replace("<", "\\" + "u003c"),
        **extra,
    }


def _structured(base: str, lang: str, page: str) -> list:
    data = [{
        "@context": "https://schema.org", "@type": "SoftwareApplication", "name": "IkaHub",
        "applicationCategory": "GameApplication", "operatingSystem": "Web, Linux, Windows",
        "url": base + C.path_of(lang, "home"), "description": C.META[lang]["home"][1],
        "offers": {"@type": "AggregateOffer", "priceCurrency": C.price_range(lang)[0], "lowPrice": C.price_range(lang)[1],
                   "highPrice": C.price_range(lang)[2], "offerCount": str(len(C.TIERS))},
    }]
    if page == "faq":
        data.append({
            "@context": "https://schema.org", "@type": "FAQPage",
            "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
                           for q, a in C.FAQ[lang]],
        })
    return data


class SitePageView(View):
    def get(self, request, lang, page):
        if lang not in C.LANGS or page not in C.PAGES:
            raise Http404()
        extra = {}
        if page == "home":
            extra = {"t": C.HOME[lang], "p": C.PRICING[lang], "faq": C.FAQ[lang][:4]}
        elif page == "pricing":
            extra = {"t": C.PRICING[lang], "tiers": C.tiers_for(lang), "above": C.ABOVE}
        elif page == "faq":
            extra = {"faq": C.FAQ[lang]}
        elif page == "contact":
            extra = {"t": C.CONTACT[lang], "sent": request.GET.get("ok") == "1"}
        elif page == "signup":
            extra = {"t": C.SIGNUP[lang], "sent": request.GET.get("ok") == "1"}
        template = f"website/legal/{page}_{lang}.html" if page in C.LEGAL_PAGES else f"website/{page}.html"
        return render(request, template, _context(request, lang, page, **extra))


class InviteRequestView(View):
    """POST /<lang>/contato|contact/ — someone asks for an invitation to the beta."""

    def post(self, request, lang):
        from apps.audit.services import client_ip, record

        if lang not in C.LANGS:
            raise Http404()
        t = C.CONTACT[lang]
        back = C.path_of(lang, "contact")
        form = {key: (request.POST.get(key) or "").strip() for key in ("email", "name", "accounts", "message")}
        if request.POST.get("website"):                       # the field people never see: a robot filled it
            return HttpResponseRedirect(back + "?ok=1")
        errors = []
        try:
            validate_email(form["email"])
        except ValidationError:
            errors.append(t["error_email"])
        ip = client_ip(request)
        key = f"site:invite-request:{ip or 'none'}"
        if not errors and cache.get(key, 0) >= REQUESTS_PER_HOUR:
            errors.append(t["error_rate"])
        if errors:
            ctx = _context(request, lang, "contact", t=t, errors=errors, form=form)
            return render(request, "website/contact.html", ctx, status=400)
        cache.add(key, 0, timeout=3600)
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=3600)
        accounts = form["accounts"] if form["accounts"] in {value for value, _label in t["accounts_choices"]} else ""
        invite_request = InviteRequest.objects.create(
            email=form["email"][:254], name=form["name"][:120], accounts=accounts, message=form["message"][:1000],
            language=lang, ip=ip,
        )
        # what the privacy policy promises: requests are not kept beyond 12 months
        InviteRequest.objects.filter(created_at__lt=timezone.now() - timedelta(days=365)).delete()
        record("invite.requested", actor_kind="anonymous", actor_label=invite_request.email, target=invite_request,
               target_label=invite_request.email)
        return HttpResponseRedirect(back + "?ok=1")


def old_terms(request):
    """The beta's first terms address keeps working."""
    return HttpResponseRedirect(C.path_of("pt", "terms"))


def robots_txt(request):
    lines = ["User-agent: *"]
    if settings.SITE_INDEXABLE:
        lines += [f"Allow: /{code}/" for code in C.LANGS] + ["Allow: /static/website/", "Disallow: /", "",
                                                            f"Sitemap: {site_url(request)}/sitemap.xml"]
    else:
        lines.append("Disallow: /")
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")


def sitemap_xml(request):
    base = site_url(request)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">']
    for page in C.PAGES:
        for lang in C.LANGS:
            out.append(f"  <url><loc>{base}{C.path_of(lang, page)}</loc>")
            for code in C.LANGS:
                out.append(f'    <xhtml:link rel="alternate" hreflang="{C.HTML_LANG[code]}" href="{base}{C.path_of(code, page)}"/>')
            out.append(f'    <xhtml:link rel="alternate" hreflang="x-default" href="{base}{C.path_of(C.X_DEFAULT, page)}"/>')
            out.append("  </url>")
    out.append("</urlset>")
    return HttpResponse("\n".join(out) + "\n", content_type="application/xml; charset=utf-8")


# --- Visit statistics: Umami runs inside the server and is never published; the hub hands out
# only its script and forwards the count, so visitors talk to one address and no cookie is set.

def stats_script(request):
    if not (settings.UMAMI_URL and settings.UMAMI_WEBSITE_ID):
        raise Http404()
    body = cache.get("site:umami-script")
    if body is None:
        try:
            response = requests.get(f"{settings.UMAMI_URL}/script.js", timeout=3)
            response.raise_for_status()
            body = response.content
            cache.set("site:umami-script", body, timeout=3600)
        except requests.RequestException:
            logger.warning("Umami script unavailable")
            return HttpResponse("", content_type="application/javascript", status=503)
    out = HttpResponse(body, content_type="application/javascript")
    out["Cache-Control"] = "public, max-age=3600"
    return out


@csrf_exempt
def stats_send(request):
    from apps.audit.services import client_ip

    if not (settings.UMAMI_URL and settings.UMAMI_WEBSITE_ID) or request.method != "POST":
        raise Http404()
    if len(request.body) > 8192:
        return HttpResponse(status=413)
    try:
        payload = json.loads(request.body)
        if (payload.get("payload") or {}).get("website") != settings.UMAMI_WEBSITE_ID:
            return HttpResponse(status=400)                    # only this site is counted through here
    except (ValueError, AttributeError):
        return HttpResponse(status=400)
    headers = {"Content-Type": "application/json", "User-Agent": request.META.get("HTTP_USER_AGENT", "")[:300]}
    ip = client_ip(request)
    if ip:
        headers["X-Forwarded-For"] = ip
    if request.META.get("HTTP_X_UMAMI_CACHE"):
        headers["x-umami-cache"] = request.META["HTTP_X_UMAMI_CACHE"][:2000]
    try:
        upstream = requests.post(f"{settings.UMAMI_URL}/api/send", data=request.body, headers=headers, timeout=3)
    except requests.RequestException:
        return HttpResponse(status=202)                        # a lost visit never shows as an error to the visitor
    return HttpResponse(upstream.content[:4096], status=upstream.status_code,
                        content_type=upstream.headers.get("Content-Type", "application/json"))


# --- Open sign-up (N-125) ----------------------------------------------------------------------

SIGNUPS_PER_HOUR = 3


class SignupView(View):
    """POST /<lang>/cadastro|signup/enviar/ — an inactive account and the confirmation e-mail."""

    def post(self, request, lang):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.password_validation import validate_password
        from django.core.mail import send_mail

        from apps.audit.services import client_ip, record
        from apps.orgs.services import register, signup_token

        if lang not in C.LANGS:
            raise Http404()
        back = C.path_of(lang, "signup")
        if not signup_available():
            return HttpResponseRedirect(back)
        t = C.SIGNUP[lang]
        form = {key: (request.POST.get(key) or "").strip() for key in ("email", "username", "organization")}
        password = request.POST.get("password1") or ""
        if request.POST.get("website"):                       # the hidden field: a robot filled it
            return HttpResponseRedirect(back + "?ok=1")
        User = get_user_model()
        errors = []
        try:
            validate_email(form["email"])
        except ValidationError:
            errors.append(t["error_email"])
        else:
            if User.objects.filter(email__iexact=form["email"]).exists():
                errors.append(t["error_email_used"])
        try:
            User.username_validator(form["username"])
        except ValidationError:
            errors.append(t["error_username"])
        if form["username"] and User.objects.filter(username__iexact=form["username"]).exists():
            errors.append(t["error_username_used"])
        if password != (request.POST.get("password2") or ""):
            errors.append(t["error_password"])
        else:
            try:
                validate_password(password, user=User(username=form["username"], email=form["email"]))
            except ValidationError as exc:
                errors.extend(exc.messages)
        if request.POST.get("accept_terms") != "1":
            errors.append(t["error_terms"])
        ip = client_ip(request)
        key = f"site:signup:{ip or 'none'}"
        if not errors and cache.get(key, 0) >= SIGNUPS_PER_HOUR:
            errors.append(t["error_rate"])
        if errors:
            ctx = _context(request, lang, "signup", t=t, errors=errors, form=form)
            return render(request, "website/signup.html", ctx, status=400)
        cache.add(key, 0, timeout=3600)
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=3600)
        user = register(email=form["email"][:254], username=form["username"], password=password,
                        organization_name=form["organization"], ip=ip, user_agent=request.META.get("HTTP_USER_AGENT", ""))
        link = f"{site_url(request)}/conta/confirmar/{signup_token(user, form['organization'], lang)}/"
        try:
            send_mail(t["mail_subject"], t["mail_body"].format(name=user.get_username(), link=link), None, [user.email])
        except Exception:  # noqa: BLE001 - the person can sign up again; the half account is removed
            logger.exception("Sign-up e-mail could not be sent")
            user.delete()
            ctx = _context(request, lang, "signup", t=t, errors=[t["error_send"]], form=form)
            return render(request, "website/signup.html", ctx, status=503)
        record("signup.created", user=user, target=user, target_label=user.get_username())
        return HttpResponseRedirect(back + "?ok=1")


def confirm_signup_view(request, token):
    """GET /conta/confirmar/<token>/ — the e-mail is confirmed: the organization and the trial start."""
    from django.contrib.auth import login

    from apps.audit.services import record
    from apps.orgs.services import confirm_signup

    result = confirm_signup(token)
    if result is None:
        return render(request, "website/confirm_failed.html", _context(request, "pt", "signup"), status=410)
    user, organization, _lang = result
    record("signup.confirmed", user=user, target=organization, target_label=organization.name, organization=organization)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return HttpResponseRedirect("/")
