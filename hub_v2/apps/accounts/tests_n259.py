"""N-259: deleting/recreating a lobby must not orphan containers or split its add-on link."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import transaction, close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from apps.accounts import platform_agent, tests_n131
from apps.accounts.models import Account, AgentProvisionRequest, DockerHost, Node
from apps.jobs.models import Job
from apps.orgs.models import Organization


class PlatformAgentLifecycleTests(TestCase):
    setUp = tests_n131.PlatformAgentTests.setUp
    _subscribe = tests_n131.PlatformAgentTests._subscribe
    _proxies_on = tests_n131.PlatformAgentTests._proxies_on
    _agent = tests_n131.PlatformAgentTests._agent

    def start(self):
        self._subscribe(days_left=0.1)
        self._proxies_on()
        self._agent()
        self.account.refresh_from_db()
        return self.account.node

    def test_stale_repeated_provision_is_idempotent(self):
        stale = Account._base_manager.get(pk=self.account.pk)
        node = self.start()
        platform_agent.provision(stale)
        self.assertEqual(stale.node_id, node.pk)
        self.assertEqual(AgentProvisionRequest._base_manager.filter(action='create').count(), 1)
        self.assertEqual(platform_agent.accounts_with_agent(self.org), 1)

    def test_stale_end_is_idempotent_and_same_lobby_can_be_enabled_again(self):
        old = self.start()
        stale = Account._base_manager.get(pk=self.account.pk)
        platform_agent.end(self.account)
        platform_agent.end(stale)
        self.assertEqual(AgentProvisionRequest._base_manager.filter(node=old, action='remove').count(), 1)
        platform_agent.provision(self.account)
        self.assertNotEqual(self.account.node_id, old.pk)
        self.assertEqual(AgentProvisionRequest._base_manager.filter(action='create').count(), 2)

    def test_deletion_queues_removal_and_recreation_gets_a_separate_node(self):
        old = self.start()
        self.account.delete()
        old.refresh_from_db()
        self.assertFalse(old.active)
        self.assertEqual(AgentProvisionRequest._base_manager.get(action='create').status, 'failed')
        self.assertTrue(AgentProvisionRequest._base_manager.filter(node=old, action='remove', status='queued').exists())
        self.assertEqual(platform_agent.running_count(), 1)  # not free until Docker confirms removal
        new = Account._base_manager.create(node=self.own_node, organization=self.org, label='Recreated', email='c@example.com',
                                           password_enc='x', platform_proxies=True)
        platform_agent.provision(new)
        self.assertNotEqual(new.node_id, old.pk)
        self.assertEqual(platform_agent.running_count(), 2)
        removal = AgentProvisionRequest._base_manager.get(node=old, action='remove')
        removal.status = 'running'
        removal.save(update_fields=['status'])
        platform_agent.finish_request(self.host, removal.pk, 'succeeded', 'removed')
        self.assertEqual(platform_agent.running_count(), 1)

    def test_queryset_delete_also_removes_agent_and_repeated_retirement_is_safe(self):
        node = self.start()
        Account._base_manager.filter(pk=self.account.pk).delete()
        platform_agent.retire_node(node)
        self.assertEqual(AgentProvisionRequest._base_manager.filter(node=node, action='remove').count(), 1)

    def test_failed_removal_keeps_capacity_reserved(self):
        node = self.start()
        self.account.delete()
        AgentProvisionRequest._base_manager.filter(node=node, action='remove').update(status='failed')
        self.assertEqual(platform_agent.running_count(), 1)
        platform_agent.retire_node(node)
        self.assertEqual(AgentProvisionRequest._base_manager.get(node=node, action='remove').status, 'queued')

    def test_removal_keeps_capacity_after_organization_node_record_is_deleted(self):
        node = self.start()
        node_id = node.pk
        self.account.delete()
        node.delete()  # the organization cleanup deletes nodes before Docker answers
        removal = AgentProvisionRequest._base_manager.get(node_ref=node_id, action='remove')
        self.assertIsNone(removal.node_id)
        self.assertEqual(platform_agent.running_count(), 1)
        removal.status = 'running'
        removal.save(update_fields=['status'])
        platform_agent.finish_request(self.host, removal.pk, 'succeeded', 'removed')
        self.assertEqual(platform_agent.running_count(), 0)

    def test_deletion_rolls_back_removal_request_on_database_rollback(self):
        node = self.start()
        pk = self.account.pk
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.account.delete()
                raise RuntimeError('rollback')
        self.assertTrue(Account._base_manager.filter(pk=pk).exists())
        self.assertTrue(Node._base_manager.get(pk=node.pk).active)
        self.assertFalse(AgentProvisionRequest._base_manager.filter(action='remove').exists())

    def test_cannot_delete_agent_with_active_jobs(self):
        node = self.start()
        with patch('apps.jobs.signals.dispatch_job', create=True):
            Job._base_manager.create(node=node, account=self.account, action_code=100, status='running')
        pk = self.account.pk
        with self.assertRaises(ValidationError), transaction.atomic():
            self.account.delete()
        self.assertTrue(Account._base_manager.filter(pk=pk).exists())
        self.assertTrue(Node._base_manager.get(pk=node.pk).active)
        self.assertFalse(AgentProvisionRequest._base_manager.filter(action='remove').exists())

    def test_model_and_form_prevent_changing_platform_node(self):
        node = self.start()
        self.account.node = self.own_node
        with self.assertRaises(ValidationError):
            self.account.save()
        self.account.refresh_from_db()
        self.assertEqual(self.account.node_id, node.pk)
        response = self.client.post(f'/accounts/{self.account.pk}/edit/', {
            'node': self.own_node.pk, 'label': self.account.label, 'email': self.account.email, 'active': 'on'})
        self.assertEqual(response.status_code, 302)
        self.account.refresh_from_db()
        self.assertEqual(self.account.node_id, node.pk)

    def test_cannot_reuse_allocated_node_or_duplicate_the_same_lobby(self):
        node = self.start()
        with self.assertRaises(ValidationError):
            Account._base_manager.create(node=node, organization=self.org, label='Other', email='d@example.com', password_enc='x')
        duplicate = Account._base_manager.create(node=self.own_node, organization=self.org, label='Duplicate',
                                                 email=self.account.email.upper(), password_enc='x', platform_proxies=True)
        with self.assertRaisesRegex(RuntimeError, 'ja possui'):
            platform_agent.provision(duplicate)
        self.assertEqual(AgentProvisionRequest._base_manager.filter(action='create').count(), 1)

    def test_active_orphan_counts_towards_capacity_even_without_account_flags(self):
        self.start()
        Account._base_manager.filter(pk=self.account.pk).update(platform_agent=False, platform_agent_node=None)
        self.assertEqual(platform_agent.running_count(), 1)


class PlatformAgentConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_two_lobbies_cannot_reserve_the_last_slot(self):
        org = Organization.objects.create(name='Concurrent', slug='concurrent')
        machine = DockerHost._base_manager.create(name='host', organization=org, last_seen_at=timezone.now())
        own = Node._base_manager.create(name='own', organization=org, transport='https')
        accounts = [Account._base_manager.create(node=own, organization=org, label=str(i), email=f'{i}@example.com',
                                                  password_enc='x', platform_proxies=True) for i in range(2)]
        barrier = Barrier(2)

        def reserve(pk):
            close_old_connections()
            try:
                account = Account._base_manager.get(pk=pk)
                barrier.wait(timeout=10)
                try:
                    platform_agent.provision(account)
                    return 'created'
                except RuntimeError as exc:
                    if 'esgotadas' not in str(exc):
                        raise
                    return 'full'
            finally:
                close_old_connections()

        with override_settings(PLATFORM_AGENT_HOST_ID=str(machine.pk), PLATFORM_AGENT_LIMIT=1):
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(reserve, [a.pk for a in accounts]))
            self.assertCountEqual(outcomes, ['created', 'full'])
            self.assertEqual(platform_agent.running_count(), 1)
            self.assertEqual(AgentProvisionRequest._base_manager.filter(action='create').count(), 1)
