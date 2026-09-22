import json
from html.parser import HTMLParser
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Account, GameAccount, Node
from .models import Preset, PresetAction, PresetGameAccount


class Controls(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.controls = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag in ('input', 'select', 'textarea'):
            self.controls.append(dict(attrs))


class PresetConfigurationTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username='preset-test'))
        node = Node.objects.create(name='test-node')
        account = Account.objects.create(node=node, label='test', email='test@example.com')
        self.ga = GameAccount.objects.create(account=account, lobby_account_id=1,
            server_id='s1-br', server_language='br', server_number=1, name='Test')
        self.preset = Preset.objects.create(name='Donations')
        PresetGameAccount.objects.create(preset=self.preset, game_account=self.ga)
        self.action = PresetAction.objects.create(preset=self.preset, order=1,
            action_code=1006, action_name='Donation loop')
        self.url = reverse('profiles:preset-configure-action', args=[self.preset.pk, 1])
        self.cities = patch('apps.profiles.views._get_cities', return_value=[
            {'id': '11', 'name': 'Alpha', 'buildings': []},
            {'id': '22', 'name': 'Beta', 'buildings': []}])
        self.cities.start()
        self.addCleanup(self.cities.stop)

    def payload(self):
        return {'action_code': '1006', 'game_account': str(self.ga.pk),
            'donation_type': ['wood', 'tradegood'], 'donation_method': '3',
            'method_value': '12345', 'interval_minutes': '47',
            'random_wait_minutes': '0', 'target_level': '20',
            'post_production_mode': 'preserve', 'city_mode': 'per_account',
            f'cities__{self.ga.pk}': ['22']}

    def test_loop_fields_are_not_duplicated_and_values_round_trip(self):
        for code in (902, 1006):
            with self.subTest(code=code):
                self.action.action_code = code
                self.action.save()
                data = self.payload()
                data['action_code'] = str(code)
                response = self.client.post(self.url, data)
                self.assertEqual(response.status_code, 302)
                self.action.refresh_from_db()
                saved = json.loads(self.action.inputs_json)
                self.assertEqual(saved['method_value'], 12345)
                self.assertEqual(saved['interval_minutes'], 47)
                self.assertEqual(saved['random_wait_minutes'], 0)
                self.assertEqual(saved['donation_type'], ['wood', 'tradegood'])
                page = self.client.get(self.url)
                self.assertEqual(page.context['form']['method_value'].value(), 12345)
                controls = Controls(page.content.decode()).controls
                for name in ('method_value', 'interval_minutes', 'random_wait_minutes'):
                    self.assertEqual(sum(c.get('name') == name for c in controls), 1)
                selected = [c['value'] for c in controls if c.get('name') == f'cities__{self.ga.pk}' and 'checked' in c]
                self.assertEqual(selected, ['22'])

    def test_invalid_submission_preserves_saved_configuration(self):
        self.client.post(self.url, self.payload())
        self.action.refresh_from_db()
        previous = (self.action.inputs_json, self.action.per_account_json)
        data = self.payload()
        data['method_value'] = 'invalid'
        data[f'cities__{self.ga.pk}'] = ['11']
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        self.action.refresh_from_db()
        self.assertEqual((self.action.inputs_json, self.action.per_account_json), previous)
        controls = Controls(response.content.decode()).controls
        self.assertEqual([c['value'] for c in controls if c.get('name') == f'cities__{self.ga.pk}' and 'checked' in c], ['11'])

    def test_empty_explicit_city_selection_is_not_all_cities(self):
        data = self.payload()
        data[f'cities__{self.ga.pk}'] = []
        response = self.client.post(self.url, data)
        self.assertContains(response, 'Selecione ao menos uma cidade')
        self.action.refresh_from_db()
        self.assertEqual(self.action.per_account_json, '{}')
