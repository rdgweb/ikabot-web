from django.urls import path

from .views import (
    ProxyListView,
    ProxyTableView,
    ProxyCreateView,
    ProxyEditView,
    ProxyDeleteView,
    ProxyToggleView,
    ProxyTestView,
    ProxyTestAllView,
    ProxySyncView,
)

app_name = "proxy"

urlpatterns = [
    path("", ProxyListView.as_view(), name="list"),
    path("table/", ProxyTableView.as_view(), name="table"),
    path("create/", ProxyCreateView.as_view(), name="create"),
    path("sync/", ProxySyncView.as_view(), name="sync"),
    path("test-all/", ProxyTestAllView.as_view(), name="test-all"),
    path("<int:pk>/edit/", ProxyEditView.as_view(), name="edit"),
    path("<int:pk>/delete/", ProxyDeleteView.as_view(), name="delete"),
    path("<int:pk>/toggle/", ProxyToggleView.as_view(), name="toggle"),
    path("<int:pk>/test/", ProxyTestView.as_view(), name="test"),
]
