from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import Incident, Update


def published():
    return Incident.objects.filter(published_at__isnull=False)


def active():
    return published().exclude(status__in=('resolved', 'cancelled'))


@transaction.atomic
def create(data, user, publish=False):
    if publish and not data.get('confirm_publication'):
        raise ValidationError('Confirme a publicação do aviso para os clientes.')
    existing = Incident.objects.filter(pk=data['request_id']).first()
    if existing:
        return existing
    values = {k:data[k] for k in INCIDENT_FIELDS}
    now = timezone.now()
    incident = Incident.objects.create(id=data['request_id'], **values,
        published_at=now if publish else None, last_public_at=now if publish else None,
        resolved_at=now if publish and data['status'] in ('resolved','cancelled') else None)
    Update.objects.create(incident=incident, author=user, body=data['body'], status=incident.status, public=publish)
    return incident


INCIDENT_FIELDS = ('title', 'kind', 'components', 'severity', 'status', 'started_at', 'scheduled_end')


@transaction.atomic
def edit_draft(pk, data, user, publish=False):
    incident = Incident.objects.select_for_update().get(pk=pk)
    if incident.published_at or incident.revision != data.get('revision'):
        raise ValidationError('O rascunho mudou ou já foi publicado. Confira o aviso antes de continuar.')
    if publish and not data.get('confirm_publication'):
        raise ValidationError('Confirme a publicação do aviso para os clientes.')
    for name in INCIDENT_FIELDS:
        setattr(incident, name, data[name])
    now = timezone.now()
    incident.published_at = now if publish else None
    incident.last_public_at = now if publish else None
    incident.resolved_at = now if publish and incident.closed else None
    incident.revision += 1
    incident.save()
    Update.objects.create(incident=incident, author=user, body=data['body'], status=incident.status, public=publish)
    return incident


@transaction.atomic
def publish_update(pk, data, user):
    incident = Incident.objects.select_for_update().get(pk=pk)
    if incident.revision != data['revision']:
        raise ValidationError('Este aviso recebeu outra atualização. Confira o histórico antes de enviar novamente.')
    if incident.closed and incident.published_at:
        raise ValidationError('Este aviso já foi encerrado. Crie outro para um novo incidente.')
    if not data.get('confirm_publication'):
        raise ValidationError('Confirme a publicação do aviso para os clientes.')
    if data['status'] not in ('scheduled', 'cancelled') and incident.started_at > timezone.now():
        raise ValidationError('A janela ainda não começou. Publique como programada ou cancele o aviso.')
    incident.status = data['status']
    now = timezone.now()
    incident.published_at = incident.published_at or now
    incident.last_public_at = now
    incident.resolved_at = now if incident.closed else None
    incident.revision += 1
    incident.save()
    Update.objects.create(incident=incident, author=user, body=data['body'], status=incident.status, public=True)
    return incident
