from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from apps.access_control.policy import allowed
from .forms import IncidentForm, UpdateForm
from .models import Incident
from .services import active, create, edit_draft, published, publish_update


def authorize(request, write=False):
    if not allowed(request.user, 'platform.incidents.' + ('manage' if write else 'view')):
        raise Http404()


class StatusView(LoginRequiredMixin, View):
    def get(self, request):
        return render(request, 'service_status/list.html', {
            'active_incidents': active(),
            'history': Paginator(published().filter(status__in=('resolved','cancelled')), 15).get_page(request.GET.get('page')),
            'can_manage': allowed(request.user, 'platform.incidents.view')})


class DetailView(LoginRequiredMixin, View):
    def get(self, request, pk):
        incident = get_object_or_404(published(), pk=pk)
        return render(request, 'service_status/detail.html', {'incident':incident,
            'timeline':incident.updates.filter(public=True),
            'can_manage':allowed(request.user, 'platform.incidents.view')})


class ManageListView(LoginRequiredMixin, View):
    def get(self, request):
        authorize(request)
        return render(request, 'service_status/manage.html', {'incidents':Paginator(Incident.objects.all(),25).get_page(request.GET.get('page')),
            'can_write':allowed(request.user,'platform.incidents.manage')})


class CreateView(LoginRequiredMixin, View):
    def get(self, request):
        authorize(request, True)
        return render(request, 'service_status/create.html', {'form':IncidentForm(), 'TIME_ZONE_LABEL':settings.TIME_ZONE})

    def post(self, request):
        authorize(request, True)
        form = IncidentForm(request.POST)
        if form.is_valid() and request.POST.get('action') in ('draft','publish'):
            try:
                incident = create(form.cleaned_data, request.user, request.POST['action']=='publish')
                return redirect('service-status:manage-detail', pk=incident.pk)
            except (ValidationError, IntegrityError) as exc:
                form.add_error(None, exc if isinstance(exc,ValidationError) else 'O pedido já foi processado. Consulte a lista de avisos.')
        return render(request, 'service_status/create.html', {'form':form, 'TIME_ZONE_LABEL':settings.TIME_ZONE}, status=400)


class ManageDetailView(LoginRequiredMixin, View):
    def show(self, request, incident, form=None, error='', status=200):
        return render(request, 'service_status/detail.html', {'incident':incident, 'management':True,
            'timeline':incident.updates.all(), 'can_write':allowed(request.user,'platform.incidents.manage'),
            'form':form if form is not None else UpdateForm(incident=incident,initial={'revision':incident.revision,'status':incident.status}),
            'error':error}, status=status)

    def get(self, request, pk):
        authorize(request)
        return self.show(request,get_object_or_404(Incident,pk=pk))

    def post(self, request, pk):
        authorize(request, True)
        incident = get_object_or_404(Incident,pk=pk)
        form = UpdateForm(request.POST,incident=incident)
        if form.is_valid():
            try:
                publish_update(pk,form.cleaned_data,request.user)
                return redirect('service-status:manage-detail',pk=pk)
            except ValidationError as exc:
                incident.refresh_from_db()
                # Retain the message, but require review and explicit confirmation again.
                retry=UpdateForm(incident=incident,initial={'revision':incident.revision,
                    'status':form.cleaned_data['status'],'body':form.cleaned_data['body']})
                return self.show(request,incident,retry,' '.join(exc.messages),409)
        return self.show(request,incident,form,status=400)


class EditDraftView(LoginRequiredMixin, View):
    def get(self, request, pk):
        authorize(request, True)
        incident = get_object_or_404(Incident,pk=pk,published_at__isnull=True)
        form = IncidentForm(instance=incident,initial={'request_id':incident.pk,'revision':incident.revision,
            'body':incident.updates.first().body})
        return render(request,'service_status/create.html',{'form':form,'editing':True,'TIME_ZONE_LABEL':settings.TIME_ZONE})

    def post(self, request, pk):
        authorize(request, True)
        incident = get_object_or_404(Incident,pk=pk,published_at__isnull=True)
        form = IncidentForm(request.POST,instance=incident)
        if form.is_valid() and request.POST.get('action') in ('draft','publish'):
            try:
                edit_draft(pk,form.cleaned_data,request.user,request.POST['action']=='publish')
                return redirect('service-status:manage-detail',pk=pk)
            except ValidationError as exc:
                form.add_error(None,exc)
                # Keep the stale revision so resubmission cannot overwrite unseen changes.
                return render(request,'service_status/create.html',{'form':form,'editing':True,'TIME_ZONE_LABEL':settings.TIME_ZONE},status=409)
        return render(request,'service_status/create.html',{'form':form,'editing':True,'TIME_ZONE_LABEL':settings.TIME_ZONE},status=400)
