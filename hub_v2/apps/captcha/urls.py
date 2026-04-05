from django.urls import path

from .views import (
    CaptchaConfigView,
    CaptchaSaveConfigView,
    CaptchaSolveView,
    CaptchaSkipView,
    CaptchaPendingView,
)

app_name = "captcha"

urlpatterns = [
    path("", CaptchaConfigView.as_view(), name="config"),
    path("save-config/", CaptchaSaveConfigView.as_view(), name="save-config"),
    path("pending/", CaptchaPendingView.as_view(), name="pending"),
    path("<int:pk>/solve/", CaptchaSolveView.as_view(), name="solve"),
    path("<int:pk>/skip/", CaptchaSkipView.as_view(), name="skip"),
]
