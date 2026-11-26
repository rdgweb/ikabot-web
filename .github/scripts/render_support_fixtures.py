"""N-232 UI fixtures; migrated in-memory database, no external services."""
import json
import os
import sys
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'hub_v2'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.test'
import django
django.setup()
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.test import Client
from apps.orgs.models import Membership, Organization
from apps.settings_app.utils import set_setting
from apps.support.models import Ticket

call_command('migrate', verbosity=0)
set_setting('tour_enabled', '0')
User = get_user_model()
owner = User.objects.create_user('Cliente de exemplo')
staff = User.objects.create_user('Equipe de suporte', is_staff=True, is_superuser=True)
viewer = User.objects.create_user('Leitor de exemplo')
org = Organization.objects.create(name='Organização de exemplo', slug='support-browser')
for user, role in [(owner,'owner'),(viewer,'viewer')]:
    Membership.objects.filter(user=user).update(organization=org, role=role)
customer, team, reader = Client(), Client(), Client()
customer.force_login(owner); team.force_login(staff); reader.force_login(viewer)
pages = {}
def capture(name, client, path):
    for suffix, headers in [('', {}), ('_hx', {'HTTP_HX_REQUEST':'true', 'HTTP_HX_BOOSTED':'true'})]:
        response = client.get(path, **headers)
        assert response.status_code == 200, (name, response.status_code)
        pages[name + suffix] = response.content.decode()

capture('empty', customer, '/suporte/')
capture('create', customer, '/suporte/novo/')
data = {'request_id':str(uuid.uuid4()), 'subject':'Ajuda com conexão do agente', 'category':'agent',
        'body':'O agente está desconectado e preciso de ajuda para configurar a conexão.',
        'include_context':'on','context_path':'/accounts/nodes/?secret=ignored'}
assert customer.post('/suporte/novo/',data).status_code == 302
ticket = Ticket._base_manager.get(pk=data['request_id'])
url, queue_url = f'/suporte/{ticket.pk}/', f'/suporte/atendimento/{ticket.pk}/'
pages.update(customer_url=url, queue_url=queue_url, code=ticket.code)
capture('customer_open', customer, url)
capture('queue', team, '/suporte/atendimento/')
capture('team_open', team, queue_url)
def action(client, path, action, **extra):
    ticket.refresh_from_db()
    response = client.post(path, {'action':action,'revision':ticket.revision,'request_id':str(uuid.uuid4()),**extra})
    assert response.status_code == 302
action(team, queue_url, 'take')
capture('team_assigned', team, queue_url)
action(team, queue_url, 'reply', body='Confira a conexão do agente com o Hub e nos informe o resultado.')
capture('team_waiting', team, queue_url)
capture('customer_waiting', customer, url)
for suffix, headers in [('',{}),('_hx',{'HTTP_HX_REQUEST':'true','HTTP_HX_BOOSTED':'true'})]:
    response=customer.post(url,{'action':'reply','revision':1,'request_id':str(uuid.uuid4()),'body':'Meu texto continua aqui.'},**headers)
    assert response.status_code==409
    pages['stale'+suffix]=response.content.decode()
action(customer,url,'reply',body='Conferi a conexão e agora o agente está online.')
capture('customer_replied',customer,url)
capture('readonly',reader,url)
action(customer,url,'status',status='resolved')
capture('customer_resolved',customer,url)
print(json.dumps(pages))
