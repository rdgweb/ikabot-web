from django.urls import path

from .views import PiracyView, TargetSearchView

app_name = "piracy"

urlpatterns = [
    path("", PiracyView.as_view(), name="index"),
    path("alvos/", TargetSearchView.as_view(), name="target-search"),
]
