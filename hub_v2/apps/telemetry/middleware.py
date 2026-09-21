from . import services


class TelemetryMiddleware:
    """Opportunistic scheduler for the daily telemetry ping.

    The hub has no background scheduler, so every request does one cheap,
    cache-throttled check; the actual send happens in a daemon thread.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        services.tick()
        return response
