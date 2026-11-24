from django.urls import path

from .views import UserListView, UserCreateView, UserEditView, UserDeleteView
from .access_views import AccessManagementView

app_name = "users"

urlpatterns = [
    path("permissoes/", AccessManagementView.as_view(), name="access"),
    path("", UserListView.as_view(), name="list"),
    path("create/", UserCreateView.as_view(), name="create"),
    path("<int:pk>/edit/", UserEditView.as_view(), name="edit"),
    path("<int:pk>/delete/", UserDeleteView.as_view(), name="delete"),
]
