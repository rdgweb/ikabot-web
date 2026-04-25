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
