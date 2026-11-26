from django.urls import path
from .views import TicketListView, TicketCreateView, TicketDetailView

app_name = 'support'
urlpatterns = [
    path('', TicketListView.as_view(), name='list'),
    path('novo/', TicketCreateView.as_view(), name='create'),
    path('atendimento/', TicketListView.as_view(platform=True), name='queue'),
    path('atendimento/<uuid:pk>/', TicketDetailView.as_view(platform=True), name='queue-detail'),
    path('<uuid:pk>/', TicketDetailView.as_view(), name='detail'),
]
