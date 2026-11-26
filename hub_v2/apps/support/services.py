from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
from django.http import Http404
from django.utils import timezone
from apps.access_control.policy import allowed
from apps.audit.services import record
from .models import Ticket, Message, TicketEvent, Receipt


def label(user):
    return (user.get_full_name() or user.get_username())[:150]


def authorize(request, platform=False, write=False):
    capability = ('platform' if platform else 'organization') + '.support.' + ('manage' if write else 'view')
    if not allowed(request.user, capability, request.organization):
        if platform:
            raise Http404()
        raise PermissionDenied()
    if not platform and (request.organization is None or not request.organization.active):
        raise Http404()


def tickets_for(request, platform=False):
    authorize(request, platform)
    # Cross-organization reads are confined to the explicitly authorized support queue.
    qs = Ticket._base_manager.all() if platform else Ticket.objects.filter(organization=request.organization)
    return qs.select_related('organization', 'created_by', 'assigned_to')


def unread_for(user, organization=None, platform=False):
    if not user or not user.is_authenticated:
        return Ticket._base_manager.none()
    if platform:
        if not allowed(user, 'platform.support.view'):
            return Ticket._base_manager.none()
        qs, timestamp = Ticket._base_manager.all(), 'last_customer_at'
    else:
        if organization is None or not allowed(user, 'organization.support.view', organization):
            return Ticket._base_manager.none()
        qs, timestamp = Ticket.objects.filter(organization=organization, last_staff_at__isnull=False), 'last_staff_at'
    seen = Receipt._base_manager.filter(ticket_id=OuterRef('pk'), user=user, seen_at__gte=OuterRef(timestamp))
    return qs.annotate(already_seen=Exists(seen)).filter(already_seen=False).order_by('-' + timestamp)


@transaction.atomic
def create_ticket(request, data):
    authorize(request, write=True)
    existing = Ticket._base_manager.filter(pk=data['request_id']).first()
    if existing:
        if existing.organization_id == request.organization.pk and existing.created_by_id == request.user.pk:
            return existing
        raise ValidationError('Esta solicitação não pôde ser criada. Recarregue o formulário.')
    try:
        with transaction.atomic():
            ticket = Ticket._base_manager.create(id=data['request_id'], organization=request.organization, created_by=request.user,
                subject=data['subject'], category=data['category'], last_customer_at=timezone.now(),
                context_path=data['context_path'] if data['include_context'] else '',
                hub_version=settings.VERSION if data['include_context'] else '')
    except IntegrityError:
        existing = Ticket._base_manager.get(pk=data['request_id'])
        if existing.organization_id == request.organization.pk and existing.created_by_id == request.user.pk:
            return existing
        raise ValidationError('Esta solicitação não pôde ser criada. Recarregue o formulário.')
    Message._base_manager.create(ticket=ticket, author=request.user, author_label=label(request.user), body=data['body'])
    TicketEvent._base_manager.create(ticket=ticket, actor=request.user, actor_label=label(request.user), description='Abriu o chamado.')
    record('support.created', user=request.user, target=ticket, organization=ticket.organization)
    return ticket


class StaleTicket(ValidationError):
    pass


@transaction.atomic
def change_ticket(request, pk, data, platform=False):
    authorize(request, platform, write=True)
    ticket = tickets_for(request, platform).select_related(None).select_for_update().get(pk=pk)
    previous = TicketEvent._base_manager.filter(pk=data['request_id']).first()
    if previous:
        if previous.ticket_id == ticket.pk and previous.actor_id == request.user.pk:
            return ticket
        raise ValidationError('Solicitação inválida. Recarregue o chamado.')
    if ticket.revision != data['revision']:
        raise StaleTicket('O chamado recebeu outra atualização. Confira o histórico e envie novamente.')
    action = data['action']
    if action == 'reply':
        Message._base_manager.create(ticket=ticket, author=request.user, author_label=label(request.user), from_staff=platform, body=data['body'])
        if platform:
            ticket.last_staff_at = timezone.now()
            ticket.status = 'waiting_customer'
        else:
            ticket.last_customer_at = timezone.now()
            ticket.status = 'open'
        description = 'Respondeu pelo atendimento.' if platform else 'Enviou uma resposta.'
    elif action in ('take', 'release') and platform:
        if action == 'release' and ticket.assigned_to_id != request.user.pk:
            raise ValidationError('Somente o responsável atual pode liberar o atendimento. Você pode assumir o chamado.')
        ticket.assigned_to = request.user if action == 'take' else None
        description = 'Assumiu o atendimento.' if action == 'take' else 'Liberou o atendimento.'
        if action == 'take' and ticket.status == 'open':
            ticket.status = 'in_progress'
    elif action == 'status':
        status = data.get('status')
        if status not in dict(Ticket.STATES) or (not platform and status not in ('open', 'resolved')):
            raise PermissionDenied()
        ticket.status = status
        description = 'Alterou o estado para ' + dict(Ticket.STATES)[status] + '.'
    else:
        raise PermissionDenied()
    TicketEvent._base_manager.create(id=data['request_id'], ticket=ticket, actor=request.user, actor_label=label(request.user), description=description)
    if platform:
        ticket.last_staff_at = timezone.now()
    else:
        ticket.last_customer_at = timezone.now()
    ticket.revision += 1
    ticket.save()
    record('support.' + ('replied' if action == 'reply' else 'changed'), user=request.user,
           target=ticket, organization=ticket.organization, details={'action':action, 'status':ticket.status})
    return ticket
