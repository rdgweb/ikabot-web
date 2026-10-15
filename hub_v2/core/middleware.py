"""
HTMX Boost middleware — extracts #page-content from full HTML responses
for boosted (hx-boost) navigation requests.

When hx-boost is active, HTMX sends HX-Boosted: true. We render the full
page normally (so all template blocks work), then strip everything outside
the #page-content div before sending the response.
"""

import re

from django.utils.deprecation import MiddlewareMixin


# Regex to extract content of <div id="page-content">...</div>
# We use a simple approach: find the opening tag and then match to the
# corresponding closing </div> by counting nesting depth.
_PAGE_CONTENT_START = re.compile(
    r'<div\s+id=["\']page-content["\'][^>]*>',
    re.IGNORECASE,
)


class HtmxBoostMiddleware(MiddlewareMixin):
    """
    For HX-Boosted requests, extract only the #page-content innerHTML
    from the full HTML response. This avoids rendering the sidebar/head/body
    wrapper while keeping all Django template block inheritance working.
    """

    def process_response(self, request, response):
        # Process any HTMX HTML response that contains a #page-content div.
        # Covers both hx-boost (HX-Boosted) and explicit hx-get/hx-post (HX-Request).
        is_htmx = (
            request.headers.get("HX-Boosted") == "true"
            or request.headers.get("HX-Request") == "true"
        )
        is_html = "text/html" in response.get("Content-Type", "")

        if not is_htmx or not is_html:
            return response

        content = response.content.decode(response.charset)

        # Find #page-content div
        match = _PAGE_CONTENT_START.search(content)
        if not match:
            return response

        # Extract innerHTML by counting div nesting
        start_pos = match.end()  # Position right after <div id="page-content">
        inner_html = self._extract_inner_html(content, start_pos)

        if inner_html is not None:
            response.content = inner_html.encode(response.charset)
            response["Content-Length"] = len(response.content)

        return response

    @staticmethod
    def _extract_inner_html(html: str, start: int) -> str | None:
        """Extract content between the opening tag (at `start`) and its
        matching closing </div>, handling nested divs."""
        depth = 1
        pos = start
        length = len(html)

        while pos < length and depth > 0:
            # Find next <div or </div>
            next_open = html.find("<div", pos)
            next_close = html.find("</div>", pos)

            if next_close == -1:
                # No closing tag found — malformed HTML
                return None

            if next_open != -1 and next_open < next_close:
                # Found a nested <div before the next </div>
                depth += 1
                pos = next_open + 4  # Skip past "<div"
            else:
                # Found </div>
                depth -= 1
                if depth == 0:
                    return html[start:next_close]
                pos = next_close + 6  # Skip past "</div>"

        return None


class MemoryWatchMiddleware:
    """N-132: notes the requests after which this process grew a lot (RSS), to find the page that swells it.

    Only the path, the method, the status and the sizes — never the query string or the body.
    The last notes stay in the cache (memwatch:events) and in the log.
    """

    GROWTH_MB = 25
    KEEP = 300

    def __init__(self, get_response):
        self.get_response = get_response
        try:
            self._page = __import__("resource").getpagesize()
        except Exception:  # noqa: BLE001
            self._page = 4096

    def _rss_mb(self) -> float:
        try:
            with open("/proc/self/statm") as fh:
                return int(fh.read().split()[1]) * self._page / 1048576
        except Exception:  # noqa: BLE001 - not Linux: nothing to watch
            return 0.0

    def __call__(self, request):
        before = self._rss_mb()
        response = self.get_response(request)
        after = self._rss_mb()
        grew = after - before
        if before and grew >= self.GROWTH_MB:
            import logging
            import time

            from django.core.cache import cache

            note = {"t": int(time.time()), "path": request.path[:200], "method": request.method,
                    "status": getattr(response, "status_code", 0), "grew_mb": round(grew, 1), "rss_mb": round(after, 1)}
            logging.getLogger(__name__).warning("Memory: %s %s grew %.0f MB (process now %.0f MB)", request.method, note["path"], grew, after)
            try:
                events = cache.get("memwatch:events") or []
                events.append(note)
                cache.set("memwatch:events", events[-self.KEEP:], timeout=7 * 86400)
            except Exception:  # noqa: BLE001
                pass
        return response
