"""Navigation/mobile review against synthetic data in an in-memory database only."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch
from datetime import timedelta
from urllib.error import URLError
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'hub_v2'))
os.environ['DJANGO_SETTINGS_MODULE']='config.settings.test'
import django
django.setup()
import requests
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, RequestFactory, override_settings
from django.utils import timezone
from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.jobs.models import Job
from apps.notes.models import Note
from apps.orgs.models import Organization
from apps.profiles.models import Preset
from apps.service_status.models import Incident, Update
from apps.settings_app.utils import set_setting
from core.context_processors import nav_context

call_command('migrate',verbosity=0)
set_setting('tour_enabled','0')
root=get_user_model().objects.create_user('mobile-root',first_name='Pessoa de demonstração',is_staff=True,is_superuser=True)
org=Organization.objects.get(slug='principal')
Organization.objects.create(name='Outra organização de demonstração',slug='mobile-other')
node=Node.objects.create(organization=org,name='Servidor de demonstração com um nome comprido',agent_last_seen_at=timezone.now())
account=Account.objects.create(organization=org,node=node,label='Lobby de demonstração com nome comprido',email='conta-de-exemplo@example.com',password_enc='')
ga=GameAccount.objects.create(account=account,lobby_account_id=123,server_id='s78-br',server_language='br',server_number=78,name='Jogador de demonstração com nome comprido')
AccountSnapshot.objects.create(account=account,game_account=ga,base_snapshot={'gold':1234567,'income':1234,'research_points':98765},
    cities=[{'id':'42','name':'Cidade de demonstração com um nome bastante comprido','wood':123456,'wine':65432,'marble':34567,'crystal':23456,'sulfur':87654,
             'x':33,'y':44,'island_id':'501','population':2400,'max_population':3000,'tradegood':1,'current_resources':{'resource':123456,'1':65432},'buildings':[]}],
    military={},full_snapshot_updated_at=timezone.now())
job=Job.objects.create(account=account,game_account=ga,node=node,action_code=1,inputs_json='{}',status='finished',finished_at=timezone.now())
preset=Preset.objects.create(organization=org,name='Preset de demonstração para revisar o telefone')
note=Note.objects.create(title='Nota de demonstração para revisar o telefone',body='Texto de exemplo com informações suficientes para avaliar a leitura e os formulários.',status='authorized')
incident=Incident.objects.create(title='Atraso na execução de rotinas em acompanhamento pela equipe',kind='incident',components=['hub','agents','jobs'],severity='degraded',status='investigating',started_at=timezone.now(),published_at=timezone.now(),last_public_at=timezone.now())
Update.objects.create(incident=incident,author=root,body='Estamos investigando a lentidão e acompanhando a recuperação do serviço. Novas informações serão publicadas nesta página.',status='investigating',public=True)
draft=Incident.objects.create(title='Manutenção programada de demonstração',kind='maintenance',components=['hub'],severity='info',status='scheduled',started_at=timezone.now()+timedelta(days=1),scheduled_end=timezone.now()+timedelta(days=1,hours=2))
Update.objects.create(incident=draft,author=root,body='Rascunho interno de demonstração para conferir o formulário.',status='scheduled')
client=Client(); client.force_login(root)
request=RequestFactory().get('/'); request.user=root; request.organization=org; request.session={}
nav=nav_context(request)['NAV_SECTIONS']
paths=list(dict.fromkeys(item['url'] for section in nav for item in section['items']))
paths += ['/accounts/create/','/accounts/nodes/create/','/profiles/presets/new/','/proxy/create/','/notes/nova/','/suporte/novo/',
          '/settings/incidentes/novo/',f'/situacao/{incident.pk}/',f'/settings/incidentes/{incident.pk}/',f'/settings/incidentes/{draft.pk}/',f'/settings/incidentes/{draft.pk}/editar/',
          '/intel/islands/','/intel/map/','/intel/targets/','/intel/dumps/',
          '/conta/','/conta/?tab=security','/conta/?tab=preferences',f'/accounts/{account.pk}/',f'/accounts/nodes/{node.pk}/',
          f'/profiles/presets/{preset.pk}/',f'/jobs/{job.pk}/',f'/notes/{note.pk}/']
result={'pages':{},'partials':{},'status':{},'navigation':paths,'job_forms':{},'schema':None}
with override_settings(SITE_URL='http://mobile.test',UMAMI_URL='',UMAMI_WEBSITE_ID=''), \
     patch('apps.jobs.signals.dispatch_job'), \
     patch('requests.sessions.Session.request',side_effect=requests.ConnectionError('isolated mobile fixtures')), \
     patch('urllib.request.urlopen',side_effect=URLError('isolated mobile fixtures')):
    for path in paths:
        response=client.get(path,follow=True)
        result['status'][path]=response.status_code
        if response.status_code==200:
            result['pages'][path]=response.content.decode()
            result['partials'][path]=client.get(path,follow=True,HTTP_HX_REQUEST='true',HTTP_HX_BOOSTED='true').content.decode()
    for action in [1,1002,18,821]:
        path=f'/jobs/new/form/?ga={ga.pk}&action={action}'
        response=client.get(path)
        result['job_forms'][str(action)]={'status':response.status_code,'html':response.content.decode()}
    # Browser rendering of Scalar needs its local schema, not a real API connection.
    result['schema']=client.get('/api/schema/').json()
    result['status_detail']=f'/settings/incidentes/{incident.pk}/'
    invalid={'request_id':str(draft.pk),'title':'Aviso preservado no formulário','kind':'incident','components':['jobs'],'severity':'degraded','status':'investigating','started_at':timezone.localtime().strftime('%Y-%m-%dT%H:%M'),'body':'Texto para os clientes preservado após a validação.','action':'publish'}
    result['status_invalid']=client.post('/settings/incidentes/novo/',invalid,HTTP_HX_REQUEST='true',HTTP_HX_BOOSTED='true').content.decode()
    result['status_stale']=client.post(result['status_detail'],{'revision':99999,'status':'monitoring','body':'Mensagem preservada após o erro de validação.','confirm_publication':'on'},HTTP_HX_REQUEST='true',HTTP_HX_BOOSTED='true').content.decode()
print(json.dumps(result))
