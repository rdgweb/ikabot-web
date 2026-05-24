from django.urls import path

from .api import (
    BankConfigView,
    BankCycleCompleteView,
    BankCycleCreateView,
    BankCycleStatusView,
    BankTaskUpdateView,
    BankBuyCompleteView,
)

app_name = "agent-generals-bank"

urlpatterns = [
    path("generals-bank/configs/<uuid:config_id>/", BankConfigView.as_view(), name="config-detail"),
    path("generals-bank/cycles/create/", BankCycleCreateView.as_view(), name="cycle-create"),
    path("generals-bank/cycles/<uuid:cycle_id>/status/", BankCycleStatusView.as_view(), name="cycle-status"),
    path("generals-bank/cycles/<uuid:cycle_id>/buy-complete/", BankBuyCompleteView.as_view(), name="cycle-buy-complete"),
    path("generals-bank/cycles/<uuid:cycle_id>/complete/", BankCycleCompleteView.as_view(), name="cycle-complete"),
    path("generals-bank/tasks/<uuid:task_id>/update/", BankTaskUpdateView.as_view(), name="task-update"),
]
