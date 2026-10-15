"""Public site (N-119): /pt/... and /en/..., each page under its own word in each language."""
from django.urls import path

from . import content as C
from . import views

urlpatterns = []
for _lang in C.LANGS:
    for _page in C.PAGES:
        _slug = C.SLUGS[_lang][_page]
        urlpatterns.append(path(f"{_lang}/{_slug + '/' if _slug else ''}", views.SitePageView.as_view(),
                                {"lang": _lang, "page": _page}, name=f"site-{_lang}-{_page}"))
    urlpatterns.append(path(f"{_lang}/{C.SLUGS[_lang]['contact']}/enviar/", views.InviteRequestView.as_view(),
                            {"lang": _lang}, name=f"site-{_lang}-invite-request"))
    urlpatterns.append(path(f"{_lang}/{C.SLUGS[_lang]['signup']}/enviar/", views.SignupView.as_view(),
                            {"lang": _lang}, name=f"site-{_lang}-signup-send"))

urlpatterns += [
    path("conta/confirmar/<str:token>/", views.confirm_signup_view, name="signup-confirm"),
    path("robots.txt", views.robots_txt, name="robots-txt"),
    path("sitemap.xml", views.sitemap_xml, name="sitemap-xml"),
    path("stats/script.js", views.stats_script, name="stats-script"),
    path("stats/api/send", views.stats_send, name="stats-send"),
]
