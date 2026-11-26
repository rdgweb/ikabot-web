"""N-204: the eye in the top bar blurs the personal data on every page, remembered per user."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.users.models import UserProfile


class PrivacyToggleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username="rdg", password="x", email="r@example.com")
        self.client.force_login(self.user)

    def _body(self):
        page = self.client.get("/conta/").content.decode()
        return page[page.index("<body"):page.index(">", page.index("<body"))]

    def test_the_choice_is_kept_for_the_user_and_marks_every_page(self):
        self.assertNotIn("ik-private", self._body())
        response = self.client.post(reverse("account-privacy"), {"hide": "1"})
        self.assertEqual(response.json(), {"hide": True})
        self.assertTrue(UserProfile.objects.get(user=self.user).hide_private)
        self.assertIn("ik-private", self._body())
        self.client.post(reverse("account-privacy"), {"hide": "0"})
        self.assertNotIn("ik-private", self._body())

    def test_saving_other_preferences_keeps_it(self):
        self.client.post(reverse("account-privacy"), {"hide": "1"})
        self.client.post("/conta/", {"action": "preferences", "compact_tables": "on"})
        profile = UserProfile.objects.get(user=self.user)
        self.assertTrue(profile.hide_private and profile.compact_tables)

    def test_only_a_logged_in_user(self):
        self.client.logout()
        self.assertEqual(self.client.post(reverse("account-privacy"), {"hide": "1"}).status_code, 302)
