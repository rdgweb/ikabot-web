from django.urls import path
from .views import StatusView, DetailView, ManageListView, ManageDetailView, CreateView, EditDraftView

app_name = 'service-status'
urlpatterns = [
    path('situacao/', StatusView.as_view(), name='list'),
    path('situacao/<uuid:pk>/', DetailView.as_view(), name='detail'),
    path('settings/incidentes/', ManageListView.as_view(), name='manage'),
    path('settings/incidentes/novo/', CreateView.as_view(), name='create'),
    path('settings/incidentes/<uuid:pk>/editar/', EditDraftView.as_view(), name='edit'),
    path('settings/incidentes/<uuid:pk>/', ManageDetailView.as_view(), name='manage-detail'),
]
