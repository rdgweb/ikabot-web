from django.urls import path

from .views import (
    BankCycleStatusPartialView,
    BankCreateView,
    BankDetailView,
    BankHistoryPartialView,
    BankListView,
    BankProducerAddView,
    BankProducerRemoveView,
    BankProducerUpdateView,
    BankStartCycleView,
    BankUpdateView,
)

app_name = "generals_bank"

urlpatterns = [
    path("", BankListView.as_view(), name="list"),
    path("novo/", BankCreateView.as_view(), name="create"),
    path("<uuid:pk>/", BankDetailView.as_view(), name="detail"),
    path("<uuid:pk>/editar/", BankUpdateView.as_view(), name="update"),
    path("<uuid:pk>/produtores/adicionar/", BankProducerAddView.as_view(), name="producer-add"),
    path("<uuid:pk>/produtores/<uuid:producer_pk>/editar/", BankProducerUpdateView.as_view(), name="producer-update"),
    path("<uuid:pk>/produtores/<uuid:producer_pk>/remover/", BankProducerRemoveView.as_view(), name="producer-remove"),
    path("<uuid:pk>/iniciar-ciclo/", BankStartCycleView.as_view(), name="start-cycle"),
    path("<uuid:pk>/ciclo-status/", BankCycleStatusPartialView.as_view(), name="cycle-status-partial"),
    path("<uuid:pk>/historico/", BankHistoryPartialView.as_view(), name="history-partial"),
]
