"""N-205: log in with the username or the e-mail."""
from django.contrib.auth import get_user_model
from django.test import TestCase


class LoginTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("carcereiro", email="Carc@Example.com", password="senha-boa-123")

    def test_username_and_email_both_work(self):
        self.assertTrue(self.client.login(username="carcereiro", password="senha-boa-123"))
        self.client.logout()
        self.assertTrue(self.client.login(username="carc@example.com", password="senha-boa-123"))
        self.client.logout()
        response = self.client.post("/login/", {"username": "carc@example.com", "password": "senha-boa-123"})
        self.assertEqual(response.status_code, 302)

    def test_wrong_password_and_shared_email_fail(self):
        self.assertFalse(self.client.login(username="carc@example.com", password="errada"))
        get_user_model().objects.create_user("outro", email="carc@example.com", password="senha-boa-123")
        self.assertFalse(self.client.login(username="carc@example.com", password="senha-boa-123"))   # ambiguous

    def test_a_username_that_is_an_email_wins(self):
        get_user_model().objects.create_user("x@example.com", email="y@example.com", password="a-senha-dele-1")
        self.assertTrue(self.client.login(username="x@example.com", password="a-senha-dele-1"))
