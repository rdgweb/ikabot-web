"""URLs of the game session gateway (N-72): every path belongs to the session named by the host."""

from django.urls import re_path

from apps.manual_control.gateway import serve

urlpatterns = [
    re_path(r"^(?P<path>.*)$", serve),
]
