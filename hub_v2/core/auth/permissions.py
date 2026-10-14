"""
DRF permission classes for role-based access control.
"""

from rest_framework.permissions import BasePermission


class IsAgent(BasePermission):
    """Allow access only to authenticated agent workers."""

    def has_permission(self, request, view):
        if request.auth != "agent":
            return False
        bound = getattr(request._request, "agent_node_id", None)
        if bound is None:
            return True                       # the installation's single key (being phased out)
        # N-106: an agent with its own key only speaks for its own node
        mentioned = {
            str(request.META.get("HTTP_X_AGENT_NODE_ID", "") or "").strip(),
            str(request.query_params.get("node_id", "") or "").strip(),
        }
        data = getattr(request, "data", None)
        if isinstance(data, dict):
            mentioned.add(str(data.get("node_id", "") or "").strip())
        node_kwarg = (getattr(view, "kwargs", None) or {}).get("node_id")
        if node_kwarg:
            mentioned.add(str(node_kwarg))
        mentioned.discard("")
        return mentioned <= {bound}


class IsAdminUser(BasePermission):
    """Allow access only to admin users (staff or superuser)."""

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.is_staff
        )


class IsOperator(BasePermission):
    """Allow access to operators (admin group or operator group)."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_staff:
            return True
        return request.user.groups.filter(name="operator").exists()


class IsViewer(BasePermission):
    """Allow access to viewers (any authenticated user)."""

    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated
