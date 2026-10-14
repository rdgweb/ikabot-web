"""Search engines see the public site only (N-119): the panel, the API and everything else say noindex."""
from django.conf import settings

SITE_PREFIXES = ("/pt/", "/en/", "/static/website/")


class SearchEnginesMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        path = request.path
        if path in ("/robots.txt", "/sitemap.xml"):
            return response
        if not (settings.SITE_INDEXABLE and path.startswith(SITE_PREFIXES)):
            response.setdefault("X-Robots-Tag", "noindex, nofollow")
        return response
