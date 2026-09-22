import json
from html.parser import HTMLParser
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.jobs.models import Job
from .models import Preset, PresetAction, PresetGameAccount
from .services import execute_preset


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


class ExecutePresetFanoutTests(TestCase):
    """N-78 follow-up: execute_preset() must fan a multi-city + multi-donation_type
    preset action out into one job per (city, donation_type) pair — donate_loop
    (ac=902/1006) requires a singular city_id and a singular donation_type per job,
    it does not accept the plural "cities"/"donation_type" lists the preset form
    saves. Dumping the lists into one job is exactly what produced 10 "city_id nao
    informado" errors when a real 10-account preset was executed."""

    def setUp(self):
        node = Node.objects.create(name='test-node')
        account = Account.objects.create(node=node, label='test', email='test@example.com')
        self.ga = GameAccount.objects.create(account=account, lobby_account_id=1,
            server_id='s1-br', server_language='br', server_number=1, name='Test')
        AccountSnapshot.objects.create(account=account, game_account=self.ga, cities=[
            {'id': '11', 'name': 'Alpha'}, {'id': '22', 'name': 'Beta'}, {'id': '33', 'name': 'Gamma'},
        ])
        self.preset = Preset.objects.create(name='Donations')
        PresetGameAccount.objects.create(preset=self.preset, game_account=self.ga)

    def _donation_jobs(self):
        return Job.objects.filter(game_account=self.ga, action_code=1006).order_by('created_at')

    def test_all_cities_mode_fans_out_by_city_and_donation_type(self):
        PresetAction.objects.create(
            preset=self.preset, order=1, action_code=1006, action_name='Donation loop',
            inputs_json=json.dumps({
                'donation_type': ['wood', 'tradegood'], 'donation_method': '2',
                'method_value': 50, 'interval_minutes': 1440, 'random_wait_minutes': 60,
                'post_production_mode': 'preserve',
            }),
            per_account_json=json.dumps({'__mode__': 'all'}),
        )
        jobs_created, errors = execute_preset(self.preset)
        self.assertEqual(errors, [])
        self.assertEqual(jobs_created, 6)  # 3 cities x 2 donation types

        jobs = self._donation_jobs()
        self.assertEqual(jobs.count(), 6)
        seen = set()
        for job in jobs:
            inputs = json.loads(job.inputs_json)
            self.assertIn(inputs['city_id'], {'11', '22', '33'})
            self.assertIn(inputs['donation_type'], {'wood', 'tradegood'})
            self.assertNotIn('cities', inputs)  # plural key must not leak into the job
            self.assertEqual(inputs['method_value'], 50)
            seen.add((inputs['city_id'], inputs['donation_type']))
        self.assertEqual(len(seen), 6)  # every (city, donation_type) pair is unique

    def test_per_account_explicit_cities_fan_out_too(self):
        PresetAction.objects.create(
            preset=self.preset, order=1, action_code=1006, action_name='Donation loop',
            inputs_json=json.dumps({
                'donation_type': ['wood'], 'donation_method': '2', 'method_value': 50,
                'interval_minutes': 1440, 'random_wait_minutes': 60, 'post_production_mode': 'preserve',
            }),
            per_account_json=json.dumps({'__mode__': 'per_account', str(self.ga.pk): {'cities': ['11', '22']}}),
        )
        jobs_created, errors = execute_preset(self.preset)
        self.assertEqual(errors, [])
        self.assertEqual(jobs_created, 2)
        city_ids = sorted(json.loads(j.inputs_json)['city_id'] for j in self._donation_jobs())
        self.assertEqual(city_ids, ['11', '22'])

    def test_action_without_fanout_keeps_the_full_city_list_in_one_job(self):
        """ac=27 (adjust scientists) plans across all selected cities in a single
        job by design — must not be fanned out like donate_loop."""
        PresetAction.objects.create(
            preset=self.preset, order=1, action_code=27, action_name='Adjust scientists',
            inputs_json=json.dumps({'target_mode': 'absolute', 'target_value': 5, 'reserve_citizens': 0}),
            per_account_json=json.dumps({'__mode__': 'all'}),
        )
        jobs_created, errors = execute_preset(self.preset)
        self.assertEqual(errors, [])
        self.assertEqual(jobs_created, 1)
        job = Job.objects.get(game_account=self.ga, action_code=27)
        inputs = json.loads(job.inputs_json)
        self.assertEqual(sorted(inputs['cities']), ['11', '22', '33'])


class PresetConfigureActionFormNestingTests(TestCase):
    """The unconditional </div> at the end of jobs/forms/_generic_fields.html
    (meant to close a wrapper only present in the non-preset job-creation
    modal) used to render even when no_footer=True. Real browsers implicitly
    closed the still-open preset <form> while adopting it, kicking the
    Cidades section and the Salvar button entirely out of the form — no
    request fired on submit/Enter, no console error, no validation tooltip.
    It only "worked" for actions whose own field divs happened to already be
    unbalanced in a way that absorbed the stray tag first; ac=3 (many fields,
    everything cleanly balanced) reproduces it every time."""

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username='nesting-test'))
        node = Node.objects.create(name='test-node')
        account = Account.objects.create(node=node, label='test', email='nesting@example.com')
        self.ga = GameAccount.objects.create(account=account, lobby_account_id=1,
            server_id='s1-br', server_language='br', server_number=1, name='Test')
        self.preset = Preset.objects.create(name='Distribute')
        PresetGameAccount.objects.create(preset=self.preset, game_account=self.ga)
        PresetAction.objects.create(preset=self.preset, order=1, action_code=3, action_name='Distribuir Recursos')
        self.url = reverse('profiles:preset-configure-action', args=[self.preset.pk, 1])
        self.cities = patch('apps.profiles.views._get_cities', return_value=[
            {'id': '11', 'name': 'Alpha', 'buildings': []}])
        self.cities.start()
        self.addCleanup(self.cities.stop)

    def test_salvar_button_and_cidades_section_are_inside_the_form(self):
        html = self.client.get(self.url).content.decode()

        form_start = html.index('<form method="post">')
        form_end = html.index('</form>', form_start)
        salvar_pos = html.index('Salvar configuracao')
        cidades_pos = html.index('Cidades</span>')

        self.assertTrue(
            form_start < cidades_pos < form_end,
            "the Cidades section must be inside the preset <form>",
        )
        self.assertTrue(
            form_start < salvar_pos < form_end,
            "the Salvar button must be inside the preset <form> or clicking it "
            "(or pressing Enter in any field) submits nothing at all",
        )
