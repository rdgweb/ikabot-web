from django.urls import path

from .views import UserListView, UserCreateView, UserEditView, UserDeleteView

app_name = "users"

urlpatterns = [
    path("", UserListView.as_view(), name="list"),
    path("create/", UserCreateView.as_view(), name="create"),
    path("<int:pk>/edit/", UserEditView.as_view(), name="edit"),
    path("<int:pk>/delete/", UserDeleteView.as_view(), name="delete"),
]
