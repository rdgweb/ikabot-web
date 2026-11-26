import uuid
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from apps.access_control.policy import allowed
from .forms import TicketForm, ReplyForm, ChangeForm
from .models import Ticket, Message, TicketEvent, Receipt
from .services import authorize, tickets_for, unread_for, create_ticket, change_ticket


def common(request, platform):
    from apps.service_status.services import active
    prefix = 'platform' if platform else 'organization'
    return {'platform_queue': platform, 'support_org':request.organization, 'service_incidents':list(active()[:10]),
            'can_write': allowed(request.user, prefix + '.support.manage', request.organization),
            'can_queue': allowed(request.user, 'platform.support.view'), 'states': Ticket.STATES,
            'categories': Ticket.CATEGORIES, 'support_home': reverse('support:queue' if platform else 'support:list')}


class TicketListView(LoginRequiredMixin, View):
    platform = False

    def get(self, request):
        qs = tickets_for(request, self.platform)
        counts = dict(qs.values('status').annotate(total=Count('pk')).values_list('status', 'total'))
        status = request.GET.get('status', 'active')
        if status in dict(Ticket.STATES):
            qs = qs.filter(status=status)
        elif status != 'all':
            status = 'active'
            qs = qs.exclude(status='resolved')
        category = request.GET.get('category', '')
        if category in dict(Ticket.CATEGORIES):
            qs = qs.filter(category=category)
        else:
            category = ''
        q = request.GET.get('q', '').strip()[:160]
        if q:
            query = Q(subject__icontains=q)
            code = q.removeprefix('SUP-').replace('-', '')
            if len(code) >= 6 and all(c in '0123456789abcdefABCDEF' for c in code):
                query |= Q(id__istartswith=code)
            if self.platform:
                query |= Q(organization__name__icontains=q)
            qs = qs.filter(query)
        mine = request.GET.get('assigned', '') if self.platform else ''
        if mine == 'me':
            qs = qs.filter(assigned_to=request.user)
        elif mine == 'unassigned':
            qs = qs.filter(assigned_to__isnull=True)
        page = Paginator(qs, 25).get_page(request.GET.get('page'))
        unread = set(unread_for(request.user, request.organization, self.platform).filter(pk__in=[t.pk for t in page]).values_list('pk', flat=True))
        for ticket in page:
            ticket.is_unread = ticket.pk in unread
            ticket.detail_url = reverse('support:queue-detail' if self.platform else 'support:detail', args=[ticket.pk])
        querystring = request.GET.copy()
        querystring.pop('page', None)
        return render(request, 'support/list.html', {**common(request, self.platform), 'tickets': page,
            'counts': counts, 'active_count': sum(v for k, v in counts.items() if k != 'resolved'),
            'q': q, 'status_filter':status, 'category_filter':category, 'assigned_filter':mine,
            'querystring':querystring.urlencode()})


class TicketCreateView(LoginRequiredMixin, View):
    def get(self, request):
        authorize(request, write=True)
        return render(request, 'support/create.html', {**common(request, False), 'form':TicketForm()})

    def post(self, request):
        authorize(request, write=True)
        form = TicketForm(request.POST)
        if form.is_valid():
            try:
                ticket = create_ticket(request, form.cleaned_data)
                messages.success(request, 'Chamado aberto. Você pode acompanhar as respostas por aqui e pelo sino.')
                return redirect('support:detail', pk=ticket.pk)
            except ValidationError as exc:
                form.add_error(None, exc)
        return render(request, 'support/create.html', {**common(request, False), 'form':form}, status=400)


class TicketDetailView(LoginRequiredMixin, View):
    platform = False

    def show(self, request, ticket, reply=None, error='', status=200):
        now = timezone.now()
        pages = Paginator(Message._base_manager.filter(ticket=ticket), 30)
        page = pages.get_page(request.GET.get('page', pages.num_pages))
        if page.number == pages.num_pages:
            Receipt._base_manager.update_or_create(ticket=ticket, user=request.user, defaults={'seen_at':now})
        initial = {'revision':ticket.revision}
        return render(request, 'support/detail.html', {**common(request, self.platform), 'ticket':ticket,
            'thread':page, 'history':Paginator(TicketEvent._base_manager.filter(ticket=ticket), 25).get_page(request.GET.get('history_page')),
            'reply_form':reply if reply is not None else ReplyForm(initial=initial), 'error':error,
            'state_key':uuid.uuid4(), 'assignee_key':uuid.uuid4()}, status=status)

    def get(self, request, pk):
        return self.show(request, get_object_or_404(tickets_for(request, self.platform), pk=pk))

    def post(self, request, pk):
        authorize(request, self.platform, write=True)
        ticket = get_object_or_404(tickets_for(request, self.platform), pk=pk)
        form = ChangeForm(request.POST)
        reply = ReplyForm(request.POST) if request.POST.get('action') == 'reply' else None
        if not form.is_valid() or (reply is not None and not reply.is_valid()):
            return self.show(request, ticket, reply, 'Confira os campos da solicitação.', 400)
        data = {**form.cleaned_data, **(reply.cleaned_data if reply is not None else {})}
        try:
            change_ticket(request, pk, data, self.platform)
        except Ticket.DoesNotExist:
            raise Http404()
        except (ValidationError, IntegrityError) as exc:
            ticket = get_object_or_404(tickets_for(request, self.platform), pk=pk)
            # Keep the text, but issue a fresh token/revision after a stale response.
            retry = ReplyForm(initial={'body':data.get('body',''), 'revision':ticket.revision}) if reply is not None else None
            error = ' '.join(exc.messages) if isinstance(exc, ValidationError) else 'A solicitação não foi aplicada. Confira o histórico e tente novamente.'
            return self.show(request, ticket, retry, error, 409)
        return redirect('support:queue-detail' if self.platform else 'support:detail', pk=pk)
