import uuid
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from apps.access_control.models import AccessMode, Role, Grant
from apps.orgs.models import Organization, Membership
from apps.orgs import scope
from apps.orgs.bell import items
from .models import Ticket, Message, TicketEvent, Receipt
from .services import unread_for


@override_settings(TELEMETRY_DISABLED=True)
class SupportTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user('support-owner', password='x')
        self.other = User.objects.create_user('other-owner', password='x')
        self.reader = User.objects.create_user('support-reader', password='x')
        self.admin = User.objects.create_user('support-admin', password='x', is_superuser=True, is_staff=True)
        self.agent = User.objects.create_user('support-delegate', password='x')
        self.org = Organization.objects.create(name='Cliente A', slug='support-a')
        self.other_org = Organization.objects.create(name='Cliente B', slug='support-b')
        for user, org, role in [(self.owner,self.org,'owner'),(self.reader,self.org,'viewer'),(self.other,self.other_org,'owner')]:
            Membership.objects.filter(user=user).delete()
            Membership.objects.create(user=user, organization=org, role=role)
        role = Role.objects.create(name='Equipe de suporte', scope='platform', permissions=['platform.support.view','platform.support.manage'])
        AccessMode.objects.create(user=self.agent, scope_key='platform')
        Grant.objects.create(user=self.agent, role=role, scope_key='platform')
        self.client.force_login(self.owner)

    def create(self, **extra):
        data = {'request_id':str(uuid.uuid4()),'subject':'Problema ao acessar o painel', 'category':'access',
                'body':'Tentei entrar e preciso de ajuda para entender o erro.', **extra}
        response = self.client.post('/suporte/novo/',data)
        self.assertEqual(response.status_code,302, response.content.decode()[:1000])
        return Ticket._base_manager.get(pk=data['request_id']), data

    def action(self, ticket, action, **extra):
        ticket.refresh_from_db()
        return {'request_id':str(uuid.uuid4()),'revision':ticket.revision,'action':action, **extra}

    def detail(self, ticket, queue=False):
        return '/suporte/' + ('atendimento/' if queue else '') + str(ticket.pk) + '/'

    def test_create_context_is_opt_in_and_does_not_store_queries_or_credentials(self):
        ticket,_ = self.create(context_path='/jobs/abc/?token=secret#fragment')
        self.assertEqual((ticket.context_path,ticket.hub_version),('',''))
        ticket,_ = self.create(include_context='on',context_path='/game/?token=secret#fragment')
        self.assertEqual(ticket.context_path,'/game/')
        self.assertTrue(ticket.hub_version)
        self.assertEqual(Message.objects.filter(ticket=ticket).count(),1)
        self.assertEqual(TicketEvent.objects.filter(ticket=ticket).count(),1)
        data={'request_id':str(uuid.uuid4()),'subject':'Contexto externo','category':'general','body':'Erro com informações suficientes.',
              'include_context':'on','context_path':'https://example.com/private'}
        self.assertEqual(self.client.post('/suporte/novo/',data).status_code,400)

    def test_create_and_reply_are_idempotent_and_history_is_not_duplicated(self):
        ticket,data=self.create()
        self.assertEqual(self.client.post('/suporte/novo/',data).status_code,302)
        self.assertEqual(Ticket.objects.count(),1)
        reply=self.action(ticket,'reply',body='Mais informações sobre o problema.')
        for _ in range(2):
            self.assertEqual(self.client.post(self.detail(ticket),reply).status_code,302)
        self.assertEqual(Message.objects.filter(ticket=ticket).count(),2)
        self.assertEqual(TicketEvent.objects.filter(ticket=ticket).count(),2)

    def test_tenant_isolation_covers_urls_post_search_models_and_bell(self):
        ticket,_=self.create(subject='Assunto secreto do cliente A')
        self.client.force_login(self.other)
        for queue in [False,True]:
            url=self.detail(ticket,queue)
            self.assertEqual(self.client.get(url).status_code,404)
            self.assertEqual(self.client.post(url,self.action(ticket,'reply',body='Não autorizado')).status_code,404)
        self.assertNotContains(self.client.get('/suporte/?q=secreto'),ticket.subject)
        token=scope.activate(self.other_org)
        try:
            for model in [Ticket,Message,TicketEvent,Receipt]:
                self.assertEqual(model.objects.count(),0)
            self.assertFalse(any(ticket.code in row['title'] for row in items(user=self.other)))
        finally:
            scope.deactivate(token)
        self.assertEqual(Message._base_manager.filter(ticket=ticket).count(),1)

    def test_owner_cannot_inject_staff_actions_assignment_or_other_organization(self):
        ticket,_=self.create(organization=str(self.other_org.pk),assigned_to=str(self.admin.pk),status='resolved')
        self.assertEqual(ticket.organization_id,self.org.pk)
        self.assertIsNone(ticket.assigned_to_id)
        for action,extra in [('take',{}),('status',{'status':'in_progress'}),('reply',{'body':'Testando a origem.','from_staff':'true'})]:
            response=self.client.post(self.detail(ticket),self.action(ticket,action,**extra))
            self.assertEqual(response.status_code,302 if action=='reply' else 403)
        self.assertFalse(Message.objects.filter(from_staff=True).exists())

    def test_viewer_can_read_but_cannot_create_reply_or_change_state(self):
        ticket,_=self.create()
        self.client.force_login(self.reader)
        self.assertContains(self.client.get(self.detail(ticket)),ticket.subject)
        self.assertNotContains(self.client.get(self.detail(ticket)),'Enviar resposta')
        self.assertEqual(self.client.get('/suporte/novo/').status_code,403)
        self.assertEqual(self.client.post('/suporte/novo/',{}).status_code,403)
        self.assertEqual(self.client.post(self.detail(ticket),self.action(ticket,'status',status='resolved')).status_code,403)

    def test_dedicated_delegation_without_staff_membership_and_revocation(self):
        ticket,_=self.create()
        Membership.objects.filter(user=self.agent).delete()
        self.client.force_login(self.agent)
        self.assertContains(self.client.get('/suporte/atendimento/'),ticket.subject)
        self.assertEqual(self.client.post(self.detail(ticket,True),self.action(ticket,'take')).status_code,302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.assigned_to_id,self.agent.pk)
        self.assertEqual(ticket.status,'in_progress')
        Grant.objects.filter(user=self.agent).update(revoked_at=timezone.now())
        self.assertEqual(self.client.get('/suporte/atendimento/').status_code,404)
        self.assertEqual(self.client.post(self.detail(ticket,True),self.action(ticket,'reply',body='Revogado')).status_code,404)

    def test_reply_state_transitions_resolution_reopening_and_per_user_receipts(self):
        ticket,_=self.create()
        self.client.force_login(self.agent)
        self.assertEqual(self.client.post(self.detail(ticket,True),self.action(ticket,'reply',body='Consegue informar a tela?')).status_code,302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status,'waiting_customer')
        self.assertEqual(unread_for(self.owner,self.org).count(),1)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.detail(ticket)),'Consegue informar a tela?')
        self.assertEqual(unread_for(self.owner,self.org).count(),0)
        self.assertEqual(unread_for(self.reader,self.org).count(),1)
        self.assertEqual(self.client.post(self.detail(ticket),self.action(ticket,'reply',body='O problema ocorre na tela de jogo.')).status_code,302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status,'open')
        for status in ['resolved','open']:
            self.assertEqual(self.client.post(self.detail(ticket),self.action(ticket,'status',status=status)).status_code,302)
            ticket.refresh_from_db()
            self.assertEqual(ticket.status,status)

    def test_stale_revision_does_not_overwrite_or_lose_the_response_text(self):
        ticket,_=self.create()
        old=self.action(ticket,'reply',body='Texto que deve ser preservado.')
        self.assertEqual(self.client.post(self.detail(ticket),self.action(ticket,'status',status='resolved')).status_code,302)
        response=self.client.post(self.detail(ticket),old)
        self.assertContains(response,'Texto que deve ser preservado.',status_code=409)
        self.assertContains(response,'recebeu outra atualização',status_code=409)
        self.assertEqual(Message.objects.filter(ticket=ticket).count(),1)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status,'resolved')

    def test_expired_plan_can_contact_support_without_unlocking_game_work(self):
        from apps.billing.models import BillingAccount
        BillingAccount.objects.create(organization=self.org,trial_ends_at=timezone.now()-timedelta(days=50))
        ticket,_=self.create()
        self.assertEqual(self.client.post(self.detail(ticket),self.action(ticket,'reply',body='Preciso de ajuda com o plano.')).status_code,302)
        self.assertEqual(self.client.post('/jobs/create/',{}).status_code,402)

    def test_bell_contains_support_link_without_exposing_other_tenants_and_handles_resolution(self):
        ticket,_=self.create()
        self.client.force_login(self.agent)
        self.assertContains(self.client.get('/avisos/'),ticket.code)
        self.assertEqual(self.client.post(self.detail(ticket,True),self.action(ticket,'status',status='resolved')).status_code,302)
        self.client.force_login(self.owner)
        response=self.client.get('/avisos/')
        self.assertContains(response,self.detail(ticket))
        self.client.get(self.detail(ticket))
        self.assertNotContains(self.client.get('/avisos/'),ticket.code)

    def test_rendering_escapes_messages_and_paginates_threads_history_and_lists(self):
        ticket,_=self.create(body='<script>alert("xss")</script> Descrição do problema.')
        for i in range(32):
            Message._base_manager.create(ticket=ticket,author=self.owner,author_label='Cliente',body=f'Mensagem {i}')
            TicketEvent._base_manager.create(ticket=ticket,actor=self.owner,actor_label='Cliente',description=f'Evento {i}')
        response=self.client.get(self.detail(ticket))
        self.assertEqual(len(response.context['thread']),3)
        self.assertEqual(len(response.context['history']),25)
        response=self.client.get(self.detail(ticket)+'?page=1&history_page=2')
        self.assertContains(response,'&lt;script&gt;')
        self.assertNotContains(response,'<script>alert("xss")</script>')
        self.assertEqual(len(response.context['history']),8)

    def test_authentication_csrf_and_invalid_submissions(self):
        self.client.logout()
        self.assertEqual(self.client.get('/suporte/').status_code,302)
        secure=Client(enforce_csrf_checks=True)
        secure.force_login(self.owner)
        self.assertEqual(secure.post('/suporte/novo/',{}).status_code,403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post('/suporte/novo/',{'body':'curto'}).status_code,400)
        self.assertEqual(Ticket.objects.count(),0)

    def test_support_messages_fields_and_bell_respect_the_visibility_preference(self):
        from apps.users.models import UserProfile
        ticket,_=self.create()
        UserProfile.objects.update_or_create(user=self.owner,defaults={'hide_private':True})
        page=self.client.get(self.detail(ticket))
        self.assertContains(page,'sup-message-body ik-pv')
        self.assertContains(page,'class="ik-pv"')
        self.assertIn('ik-private',page.content.decode().split('<body',1)[1].split('>',1)[0])
        self.client.force_login(self.agent)
        self.assertContains(self.client.get('/avisos/'),'class="ik-pv"')

    def test_audit_records_ticket_actions_without_copying_conversation_content(self):
        from apps.audit.models import AuditEvent
        ticket,_=self.create(body='Conteudo privado que deve ficar somente na conversa.')
        event=AuditEvent.objects.get(action='support.created',target_id=str(ticket.pk))
        self.assertEqual(event.organization_id,self.org.pk)
        self.assertEqual(event.category,'suporte')
        self.assertNotIn('Conteudo privado',str(event.details))
