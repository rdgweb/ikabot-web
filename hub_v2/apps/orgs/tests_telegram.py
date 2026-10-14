"""N-106 stage 4: each organization links its own chat and picks its notices, only for itself."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.orgs.models import Membership, Organization
from apps.orgs.services import default_organization
from apps.telegram.models import TelegramBotConfig
from apps.telegram.services.config import organization_config, platform_bot


@patch("apps.orgs.views.bot_username", return_value="IkaHubBot")
class OrganizationTelegramPageTests(TestCase):
    def setUp(self):
        bot = platform_bot()
        bot.bot_token, bot.enabled, bot.organization = "123:abc", True, default_organization()
        bot.chat_id, bot.link_status = "111", "linked"
        bot.save()
        self.org_b = Organization.objects.create(name="Outro cliente", slug="outro")
        self.customer = get_user_model().objects.create_user("cliente", password="x")
        Membership.objects.filter(user=self.customer).update(organization=self.org_b, role="owner")
        self.client.force_login(self.customer)

    def test_the_customer_sees_and_links_only_its_own_chat(self, _name):
        page = self.client.get(reverse("orgs:telegram")).content.decode()
        self.assertIn("Nenhum chat vinculado", page)                   # the platform's chat (111) is not theirs
        self.client.post(reverse("orgs:telegram"), {"action": "link"})
        page = self.client.get(reverse("orgs:telegram")).content.decode()
        config_b = organization_config(self.org_b)
        self.assertEqual(len(config_b.link_code), 10)
        self.assertIn(f"https://t.me/IkaHubBot?start={config_b.link_code}", page)
        self.assertEqual(platform_bot().link_status, "linked")         # the platform's row untouched

    def test_notices_are_saved_for_its_organization(self, _name):
        self.client.post(reverse("orgs:telegram"), {"action": "notices", "notify_job_done": "on"})
        self.assertTrue(organization_config(self.org_b).notify_job_done)
        self.assertFalse(TelegramBotConfig._base_manager.get(pk=1).notify_job_done)

    def test_a_reader_only_looks(self, _name):
        Membership.objects.filter(user=self.customer).update(role="viewer")
        self.assertEqual(self.client.get(reverse("orgs:telegram")).status_code, 200)
        self.assertEqual(self.client.post(reverse("orgs:telegram"), {"action": "link"}).status_code, 404)
        self.assertEqual(organization_config(self.org_b).link_code, "")

    def test_unlinking_touches_only_its_chat(self, _name):
        config_b = organization_config(self.org_b)
        config_b.chat_id, config_b.link_status = "222", "linked"
        config_b.save()
        self.client.post(reverse("orgs:telegram"), {"action": "unlink"})
        self.assertEqual(organization_config(self.org_b).link_status, "unlinked")
        self.assertEqual(platform_bot().chat_id, "111")


class CaptchaChoiceTests(TestCase):
    def test_the_organization_decides_and_empty_follows_the_platform(self):
        from apps.captcha.models import CaptchaConfig
        from apps.orgs.services import captcha_goes_to_telegram, preferences_of

        org = Organization.objects.create(name="Cliente", slug="cliente")
        platform = CaptchaConfig(solve_method="auto", fallback_method="manual")
        self.assertFalse(captcha_goes_to_telegram(org, platform))
        platform.fallback_method = "telegram"
        self.assertTrue(captcha_goes_to_telegram(org, platform))
        preferences = preferences_of(org)
        preferences.captcha_telegram = "off"
        preferences.save()
        self.assertFalse(captcha_goes_to_telegram(org, platform))
        preferences.captcha_telegram = "on"
        preferences.save()
        self.assertTrue(captcha_goes_to_telegram(org, CaptchaConfig(solve_method="auto", fallback_method="none")))


@patch("apps.orgs.views.bot_username", return_value="IkaHubBot")
class CaptchaChoicePageTests(TestCase):
    def test_an_owner_saves_and_a_reader_cannot(self, _name):
        org = Organization.objects.create(name="Cliente", slug="cliente")
        owner = get_user_model().objects.create_user("dono", password="x")
        Membership.objects.filter(user=owner).update(organization=org, role="owner")
        bot = platform_bot()
        bot.bot_token, bot.enabled = "123:abc", True
        bot.save()
        self.client.force_login(owner)
        self.client.post(reverse("orgs:telegram"), {"action": "captcha", "captcha_telegram": "on"})
        from apps.orgs.services import preferences_of

        self.assertEqual(preferences_of(org).captcha_telegram, "on")
        Membership.objects.filter(user=owner).update(role="viewer")
        self.assertEqual(self.client.post(reverse("orgs:telegram"), {"action": "captcha", "captcha_telegram": "off"}).status_code, 404)
        self.assertEqual(preferences_of(org).captcha_telegram, "on")
