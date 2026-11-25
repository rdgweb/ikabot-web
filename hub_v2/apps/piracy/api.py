"""Agent API of the month's plan (N-211): what the Pirate Fortress job of an account does for the plan now, and how
the raid went."""
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import GameAccount
from core.auth.backends import AgentTokenAuthentication
from core.auth.permissions import IsAgent

from . import services
from .models import PiracyRoute


class NextView(APIView):
    """GET /api/agent/piracy/next/?game_account_id= — {"route": {...} | null, "hold": bool}."""

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def get(self, request):
        ga = GameAccount._base_manager.filter(pk=str(request.query_params.get("game_account_id") or "")).first()
        if ga is None:
            return Response({"route": None, "hold": False})
        # N-254: the strength and points the agent has just read (the job's saved ones are from the last run)
        fresh = {}
        for key, param in (("crew", "crew"), ("points", "points")):
            try:
                fresh[key] = int(request.query_params[param])
            except (KeyError, TypeError, ValueError):
                pass
        return Response(services.next_for(ga, fresh=fresh))


class RouteReportView(APIView):
    """POST /api/agent/piracy/routes/<id>/ {"status": "sent"|"done"|"failed", "message": "..."}."""

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request, route_id):
        route = PiracyRoute._base_manager.filter(pk=route_id).first()
        if route is None:
            return Response({"error": "route not found"}, status=404)
        status = str(request.data.get("status") or "")
        if status not in (PiracyRoute.SENT, PiracyRoute.DONE, PiracyRoute.FAILED):
            return Response({"error": "status must be sent, done or failed"}, status=400)
        services.report(route, status, str(request.data.get("message") or ""))
        return Response({"ok": True, "status": route.status})
