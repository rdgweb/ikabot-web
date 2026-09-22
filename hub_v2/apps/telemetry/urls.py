from django.urls import path

from .views import ConsentView, DisableView, ItemsView, PreviewView, SendNowView

app_name = "telemetry"

urlpatterns = [
    path("consent/", ConsentView.as_view(), name="consent"),
    path("items/", ItemsView.as_view(), name="items"),
    path("disable/", DisableView.as_view(), name="disable"),
    path("send/", SendNowView.as_view(), name="send"),
    path("preview/", PreviewView.as_view(), name="preview"),
]
