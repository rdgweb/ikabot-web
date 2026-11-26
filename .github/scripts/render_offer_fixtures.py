"""N-243 browser fixtures, rendered against migrated in-memory data only."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'hub_v2'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.test'
import django
django.setup()
from django.core.management import call_command
from django.test import Client, override_settings
from unittest.mock import patch
from apps.billing.tests import customer
from apps.billing.services import set_billing_open

call_command('migrate', verbosity=0)
pages = {}
with override_settings(SITE_URL='http://offer.test', UMAMI_URL='', UMAMI_WEBSITE_ID=''):
    client = Client()
    cases = {
        'default': {},
        'six': {'estimate': '1', 'active_subaccounts': '6', 'lobbies': '6', 'platform_agents': '6', 'platform_proxies': '6'},
        'invalid': {'estimate': '1', 'active_subaccounts': '4', 'lobbies': '1', 'platform_agents': '2', 'platform_proxies': '0'},
    }
    for key, values in cases.items():
        pages[key] = client.get('/pt/precos/', values).content.decode()
    pages['english'] = client.get('/en/pricing/', cases['six']).content.decode()
    pages['signup'] = client.get('/pt/cadastro/').content.decode()
    set_billing_open(True)
    user, _ = customer('offer-browser')
    client.force_login(user)
    with patch('apps.billing.asaas.configured', return_value=True), \
         patch('apps.accounts.platform_agent.accounts_with_agent', return_value=6), \
         patch('apps.billing.views._addons', return_value=6):
        pages['checkout'] = client.get('/organizacao/plano/').content.decode()
print(json.dumps(pages))
