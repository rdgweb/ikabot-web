from django.urls import path

from .views import PlanView

app_name = "billing"

urlpatterns = [
    path("", PlanView.as_view(), name="plan"),
]
