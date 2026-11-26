import uuid
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, Client, skipUnlessDBFeature
from django.utils import timezone
from apps.access_control.models import Role, Grant, AccessMode
from apps.orgs.models import Organization, Membership
from .models import Incident, Update


class IncidentTests(TestCase):
    def setUp(self):
        User=get_user_model()
        self.root=User.objects.create_user('status-root',is_superuser=True,is_staff=True)
        self.customer=User.objects.create_user('status-customer')
        self.other=User.objects.create_user('status-other')
        self.delegate=User.objects.create_user('status-delegate')
        self.org=Organization.objects.create(name='Organização do cliente',slug='status-customer')
        Membership.objects.filter(user=self.customer).delete()
        Membership.objects.create(user=self.customer,organization=self.org,role='viewer')
        self.role=Role.objects.create(name='Consulta de incidentes',scope='platform',permissions=['platform.incidents.view'])
        AccessMode.objects.create(user=self.delegate,scope_key='platform')
        self.grant=Grant.objects.create(user=self.delegate,role=self.role,scope_key='platform')
        self.client.force_login(self.root)

    def data(self, **kw):
        return {'request_id':str(uuid.uuid4()),'title':'Atraso na execução das rotinas', 'kind':'incident',
            'components':['jobs','hub'], 'severity':'degraded','status':'investigating',
            'started_at':(timezone.localtime()-timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M'),
            'scheduled_end':'','body':'Estamos investigando atrasos na execução das rotinas. Acompanhe por esta página.',
            'action':'draft',**kw}

    def create(self, **kw):
        data=self.data(**kw)
        r=self.client.post('/settings/incidentes/novo/',data)
        self.assertEqual(r.status_code,302,str(r.context['form'].errors) if r.status_code==400 else r.content.decode()[:500])
        return Incident.objects.get(pk=data['request_id']),data

    def path(self, i):
        return f'/settings/incidentes/{i.pk}/'

    def publish(self,i,**kw):
        return self.client.post(self.path(i),{'revision':i.revision,'status':i.status,
            'body':'A equipe está acompanhando o problema e publicará novas informações aqui.',
            'confirm_publication':'on',**kw})

    def test_draft_is_internal_and_editable_with_history(self):
        i,data=self.create()
        data.update(title='Título corrigido antes da publicação',revision=i.revision)
        self.assertEqual(self.client.post(self.path(i)+'editar/',data).status_code,302)
        i.refresh_from_db()
        self.assertEqual(i.title,data['title'])
        self.assertEqual(i.updates.count(),2)
        self.client.force_login(self.customer)
        self.assertNotContains(self.client.get('/situacao/'),data['title'])
        self.assertEqual(self.client.get(f'/situacao/{i.pk}/').status_code,404)
        self.assertEqual(self.client.get(self.path(i)).status_code,404)
        self.assertNotContains(self.client.get('/avisos/'),data['title'])

    def test_explicit_confirmation_required_for_publication(self):
        data=self.data(action='publish')
        self.assertEqual(self.client.post('/settings/incidentes/novo/',data).status_code,400)
        self.assertFalse(Incident.objects.exists())
        i,_=self.create()
        self.assertEqual(self.publish(i,confirm_publication='').status_code,400)
        i.refresh_from_db()
        self.assertIsNone(i.published_at)

    def test_global_publication_is_visible_across_organizations_but_drafts_never_leak(self):
        i,_=self.create(action='publish',confirm_publication='on')
        self.create(title='Rascunho sigiloso')
        for user in (self.customer,self.other):
            self.client.force_login(user)
            r=self.client.get('/situacao/')
            self.assertContains(r,i.title)
            self.assertNotContains(r,'Rascunho sigiloso')
            self.assertContains(self.client.get(f'/situacao/{i.pk}/'),'Estamos investigando')
            bell=self.client.get('/avisos/')
            self.assertContains(bell,f'/situacao/{i.pk}/')
            self.assertNotContains(bell,'Rascunho sigiloso')
        self.client.logout()
        self.assertEqual(self.client.get('/situacao/').status_code,302)
        self.assertEqual(self.client.get(f'/situacao/{i.pk}/').status_code,302)

    def test_update_and_resolution_keep_history_and_stop_banner(self):
        i,_=self.create(action='publish',confirm_publication='on')
        self.assertEqual(self.publish(i,status='monitoring').status_code,302)
        i.refresh_from_db()
        self.assertEqual(self.publish(i,status='resolved',body='O serviço foi restabelecido e as rotinas estão sendo executadas normalmente.').status_code,302)
        i.refresh_from_db()
        self.assertIsNotNone(i.resolved_at)
        self.assertEqual(i.updates.filter(public=True).count(),3)
        self.assertEqual(self.publish(i).status_code,409)
        self.assertEqual(self.client.get(self.path(i)+'editar/').status_code,404)
        self.client.force_login(self.customer)
        r=self.client.get(f'/situacao/{i.pk}/')
        self.assertContains(r,'Em investigação')
        self.assertContains(r,'Em observação')
        self.assertContains(r,'Resolvido')
        self.assertNotContains(r,'status-root')
        self.assertNotContains(self.client.get('/suporte/'),'Ver situação do serviço')

    def test_support_banner_in_list_and_create_without_forcing_ticket_creation(self):
        i,_=self.create(action='publish',confirm_publication='on')
        self.client.force_login(self.customer)
        self.assertContains(self.client.get('/suporte/'),'Ver situação do serviço')
        # Readers see service state but still cannot create support tickets.
        self.assertEqual(self.client.get('/suporte/novo/').status_code,403)
        Membership.objects.filter(user=self.customer,organization=self.org).update(role='owner')
        self.assertContains(self.client.get('/suporte/novo/'),i.title)

    def test_stale_update_does_not_overwrite_or_duplicate_and_keeps_text(self):
        i,_=self.create(action='publish',confirm_publication='on')
        self.assertEqual(self.publish(i,status='identified').status_code,302)
        response=self.publish(i,status='monitoring',body='Este texto precisa continuar no formulário após o conflito.')
        self.assertEqual(response.status_code,409)
        self.assertContains(response,'Este texto precisa continuar',status_code=409)
        i.refresh_from_db()
        self.assertEqual(i.status,'identified')
        self.assertEqual(i.updates.count(),2)

    def test_create_retry_is_idempotent_and_draft_edits_reject_stale_revision(self):
        i,data=self.create()
        self.assertEqual(self.client.post('/settings/incidentes/novo/',data).status_code,302)
        self.assertEqual(i.updates.count(),1)
        data['revision']=i.revision
        self.assertEqual(self.client.post(self.path(i)+'editar/',data).status_code,302)
        self.assertEqual(self.client.post(self.path(i)+'editar/',data).status_code,409)
        self.assertEqual(i.updates.count(),2)

    def test_maintenance_window_validation_and_no_automatic_resolution(self):
        start=timezone.localtime()+timedelta(days=1)
        i,_=self.create(kind='maintenance',status='scheduled',started_at=start.strftime('%Y-%m-%d %H:%M'),
            scheduled_end=(start+timedelta(hours=2)).strftime('%Y-%m-%d %H:%M'),action='publish',confirm_publication='on')
        self.assertEqual(self.publish(i,status='in_progress').status_code,409)
        self.assertEqual(self.publish(i,status='cancelled').status_code,302)
        for extra in ({'kind':'maintenance','status':'scheduled'}, {'status':'scheduled'},
                      {'started_at':start.strftime('%Y-%m-%d %H:%M')}, {'components':['internal-secret']},
                      {'kind':'maintenance','status':'scheduled','scheduled_end':'2000-01-01 12:00'}):
            self.assertEqual(self.client.post('/settings/incidentes/novo/',self.data(**extra)).status_code,400)
        i,_=self.create(kind='maintenance',status='in_progress',scheduled_end=(timezone.localtime()-timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M'),action='publish',confirm_publication='on')
        self.assertContains(self.client.get('/situacao/'),'Em manutenção')
        self.assertIsNone(i.resolved_at)

    def test_exact_platform_capability_read_only_delegate_revocation_and_menu(self):
        i,_=self.create()
        self.client.force_login(self.delegate)
        self.assertEqual(self.client.get('/settings/incidentes/').status_code,200)
        self.assertEqual(self.client.get(self.path(i)).status_code,200)
        self.assertEqual(self.client.get('/settings/incidentes/novo/').status_code,404)
        self.assertEqual(self.publish(i).status_code,404)
        self.assertEqual(self.client.get('/settings/').status_code,404)
        self.assertContains(self.client.get('/situacao/'),'Gerenciar avisos')
        self.grant.revoked_at=timezone.now(); self.grant.save()
        self.assertEqual(self.client.get('/settings/incidentes/').status_code,404)
        self.client.force_login(self.customer)
        self.assertNotContains(self.client.get('/situacao/'),'Gerenciar avisos')

    def test_csrf_and_escaping(self):
        csrf=Client(enforce_csrf_checks=True);csrf.force_login(self.root)
        self.assertEqual(csrf.post('/settings/incidentes/novo/',self.data()).status_code,403)
        i,_=self.create(title='<script>alert(1)</script>',body='<img src=x onerror=alert(1)>',action='publish',confirm_publication='on')
        self.client.force_login(self.customer)
        r=self.client.get(f'/situacao/{i.pk}/')
        self.assertContains(r,'&lt;script&gt;')
        self.assertNotContains(r,'<img src=x')

    def test_published_draft_only_exposes_public_updates_and_time_changes_notify(self):
        i,_=self.create(body='Texto interno que ainda não foi aprovado para clientes.')
        self.assertEqual(self.publish(i).status_code,302)
        self.client.force_login(self.customer)
        self.assertNotContains(self.client.get(f'/situacao/{i.pk}/'),'Texto interno')
        self.client.get('/avisos/')
        self.assertNotContains(self.client.get('/avisos/contador/'),'ik-bell-dot')
        self.client.force_login(self.root); i.refresh_from_db();self.publish(i,status='identified')
        self.client.force_login(self.customer)
        self.assertContains(self.client.get('/avisos/contador/'),'1')


class ConcurrentUpdates(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_two_publications_with_same_revision_only_append_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.core.exceptions import ValidationError
        from django.db import close_old_connections
        from .services import publish_update
        actor=get_user_model().objects.create_user('status-concurrent')
        incident=Incident.objects.create(title='Incidente em investigação',kind='incident',components=['hub'],
            status='investigating',started_at=timezone.now(),published_at=timezone.now(),last_public_at=timezone.now())
        barrier=Barrier(2)

        def send(state):
            close_old_connections()
            try:
                user=get_user_model().objects.get(pk=actor.pk)
                barrier.wait(timeout=10)
                try:
                    publish_update(incident.pk,{'revision':1,'status':state,
                        'body':'Atualização concorrente a registrar uma única vez.','confirm_publication':True},user)
                    return 'published'
                except ValidationError:
                    return 'stale'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(send,['identified','monitoring']))
        self.assertCountEqual(outcomes,['published','stale'])
        incident.refresh_from_db()
        self.assertEqual(incident.revision,2)
        self.assertEqual(incident.updates.count(),1)
