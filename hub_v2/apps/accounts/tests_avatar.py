"""N-88: imagem da conta do jogo (galeria de artes do jogo ou imagem enviada)."""

import base64

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.game.models import AccountSnapshot

from .avatars import MAX_UPLOAD_CHARS, avatar_url, clean_avatar, gallery, gallery_for_ui, initials
from .models import Account, GameAccount, Node

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60
PNG_URI = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
JPEG_URI = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 60).decode("ascii")


class AvatarRulesTests(TestCase):
    def test_initials(self):
        cases = {"HAVIT": "HA", "Lord Darkness": "LD", "BlackShadow701": "BL", "x": "X", "Fake63": "FA", "": "?", None: "?", "a b c": "AB", "701": "70"}
        for name, expected in cases.items():
            self.assertEqual(initials(name), expected, name)

    def test_gallery_has_game_art_in_groups(self):
        paths = {item["path"] for item in gallery()}
        self.assertIn("game/units/hoplita.png", paths)
        self.assertIn("game/buildings/townhall.png", paths)
        groups = gallery_for_ui()
        self.assertEqual(groups[0]["group"], "Unidades")
        self.assertTrue(all(item["url"].endswith(".png") for group in groups for item in group["items"]))

    def test_accepted_values(self):
        self.assertEqual(clean_avatar(""), "")
        self.assertEqual(clean_avatar(None), "")
        self.assertEqual(clean_avatar(" game/units/hoplita.png "), "game/units/hoplita.png")
        self.assertEqual(clean_avatar(PNG_URI), PNG_URI)
        self.assertEqual(clean_avatar(JPEG_URI), JPEG_URI)

    def test_refused_values(self):
        svg = "data:image/svg+xml;base64," + base64.b64encode(b"<svg onload=alert(1)>").decode("ascii")
        fake_png = "data:image/png;base64," + base64.b64encode(b"<script>alert(1)</script>").decode("ascii")
        too_big = "data:image/png;base64," + base64.b64encode(PNG + b"\x00" * MAX_UPLOAD_CHARS).decode("ascii")
        for value in (
            "game/units/nao_existe.png", "../../config/settings/base.py", "https://example.com/x.png",
            svg, fake_png, too_big, "data:image/png;base64,@@@", "data:text/html;base64,PGI+",
        ):
            with self.assertRaises(ValueError, msg=value[:40]):
                clean_avatar(value)


class AvatarViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="avatar", email="avatar@example.com", password="secret123")
        self.account = Account.objects.create(
            node=Node.objects.create(name="node-avatar"), label="Lobby", email="lobby@example.com", password_enc="x",
        )
        self.ga = GameAccount.objects.create(
            account=self.account, lobby_account_id=9, server_id="s9-br", server_language="br", server_number=9, name="Lord Darkness",
        )
        self.url = reverse("accounts:game-account-avatar", args=[self.ga.pk])
        self.client.force_login(self.user)

    def test_new_account_has_no_image(self):
        self.assertEqual((self.ga.avatar, avatar_url(self.ga)), ("", ""))
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_choosing_game_art(self):
        response = self.client.post(self.url, {"avatar": "game/units/hoplita.png"})

        self.assertEqual(response.status_code, 200)
        self.ga.refresh_from_db()
        self.assertEqual(self.ga.avatar, "game/units/hoplita.png")
        self.assertTrue(response.json()["url"].endswith("game/units/hoplita.png"))
        self.assertEqual(self.client.get(self.url).status_code, 404)        # gallery art is a static file

    def test_uploaded_image_is_stored_and_served(self):
        response = self.client.post(self.url, {"avatar": PNG_URI})

        self.assertEqual(response.status_code, 200)
        self.ga.refresh_from_db()
        served_url = avatar_url(self.ga)
        self.assertEqual(response.json()["url"], served_url)
        self.assertIn(f"{self.url}?v=", served_url)
        served = self.client.get(served_url)
        self.assertEqual((served.status_code, served["Content-Type"], served.content), (200, "image/png", PNG))
        self.assertIn("immutable", served["Cache-Control"])
        self.assertEqual(served["X-Content-Type-Options"], "nosniff")
        # another image gets another address, so the browser does not show the old one
        self.client.post(self.url, {"avatar": JPEG_URI})
        self.ga.refresh_from_db()
        self.assertNotEqual(avatar_url(self.ga), served_url)

    def test_removing_the_image(self):
        self.client.post(self.url, {"avatar": PNG_URI})

        response = self.client.post(self.url, {"avatar": ""})

        self.ga.refresh_from_db()
        self.assertEqual((response.status_code, self.ga.avatar, response.json()["url"]), (200, "", ""))

    def test_bad_image_changes_nothing(self):
        self.client.post(self.url, {"avatar": "game/units/hoplita.png"})

        response = self.client.post(self.url, {"avatar": "data:image/svg+xml;base64,PHN2Zz4="})

        self.ga.refresh_from_db()
        self.assertEqual((response.status_code, response.json()["ok"], self.ga.avatar), (400, False, "game/units/hoplita.png"))

    def test_needs_a_logged_user(self):
        self.client.logout()

        self.assertEqual(self.client.post(self.url, {"avatar": "game/units/hoplita.png"}).status_code, 302)
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.ga.refresh_from_db()
        self.assertEqual(self.ga.avatar, "")

    def test_game_panel_shows_the_image_and_the_picker(self):
        AccountSnapshot.objects.create(
            account=self.account, game_account=self.ga, base_snapshot={}, military={},
            cities=[{"id": "1", "name": "Capital", "tradegood": 1, "buildings": []}],
        )

        page = self.client.get(reverse("game:dashboard")).content.decode()
        self.assertIn("open-avatar-picker", page)
        self.assertIn("avatarPicker(", page)
        self.assertIn("> LD </button>", " ".join(page.split()))            # initials, not a truncated name
        self.assertIn("game/units/hoplita.png", page)                      # gallery

        self.client.post(self.url, {"avatar": PNG_URI})
        page = self.client.get(reverse("game:dashboard")).content.decode()
        self.assertIn(f'src="{avatar_url(GameAccount.objects.get(pk=self.ga.pk))}"', page.replace("&amp;", "&"))
