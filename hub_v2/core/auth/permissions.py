"""
DRF permission classes for role-based access control.
"""

from rest_framework.permissions import BasePermission


class IsAgent(BasePermission):
    """Allow access only to authenticated agent workers."""

    def has_permission(self, request, view):
        return request.auth == "agent"


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
