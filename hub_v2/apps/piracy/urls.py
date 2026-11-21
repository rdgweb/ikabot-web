from django.urls import path

from .views import PiracyView

app_name = "piracy"

urlpatterns = [
    path("", PiracyView.as_view(), name="index"),
]
