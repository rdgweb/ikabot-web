from . import services


class AuditMiddleware:
    """Keeps the events of a request and writes them if the response is not a server error (N-120)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = services.begin(request)
        write = False
        try:
            response = self.get_response(request)
            write = response.status_code < 500
            return response
        finally:
            services.finish(token, write=write)
