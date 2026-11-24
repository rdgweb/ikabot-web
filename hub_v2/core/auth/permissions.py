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
        node = getattr(request._request, "agent_node", None)
        if bound is None or node is None:
            return False
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
        if not mentioned <= {bound}:
            return False
        from .agent_policy import authorize_operation, authorize_resources
        authorize_resources(request, view, node)
        authorize_operation(request, view, node)
        return True


class IsAdminUser(BasePermission):
    """Allow access only to admin users (staff or superuser)."""

    def has_permission(self, request, view):
        from core.access import platform_admin
        from rest_framework.exceptions import NotFound
        if not platform_admin(request.user):
            raise NotFound()
        return True


class IsOperator(BasePermission):
    """Allow access to operators (admin group or operator group)."""

    def has_permission(self, request, view):
        from core.access import operator
        return operator(request.user, getattr(request._request, "organization", None))


class IsViewer(BasePermission):
    """Allow access to viewers (any authenticated user)."""

    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated
