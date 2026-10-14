"""N-119: public site — pages in pt and en, search engines see only the site, invitation requests, statistics."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.audit.models import AuditEvent

from . import content as C
from .models import InviteRequest

LEGAL = {"name": "Empresa Exemplo", "document": "CNPJ 00.000.000/0001-00", "city": "Cuiabá/MT",
         "email": "contato@example.com", "privacy_email": "privacidade@example.com", "telegram": "ikahub_suporte"}


@override_settings(SITE_URL="https://site.example", SITE_INDEXABLE=False, UMAMI_URL="", UMAMI_WEBSITE_ID="")
class SitePagesTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_every_page_answers_in_both_languages_with_its_alternates(self):
        for lang in C.LANGS:
            for page in C.PAGES:
                response = self.client.get(C.path_of(lang, page))
                self.assertEqual(response.status_code, 200, (lang, page))
                html = response.content.decode()
                self.assertIn(f'<html lang="{C.HTML_LANG[lang]}">', html)
                self.assertIn(f"<title>{C.META[lang][page][0]}</title>", html)
                self.assertIn(f'<link rel="canonical" href="https://site.example{C.path_of(lang, page)}">', html)
                for code in C.LANGS:
                    self.assertIn(f'hreflang="{C.HTML_LANG[code]}" href="https://site.example{C.path_of(code, page)}"', html)
                self.assertIn("Gameforge", html)                             # the disclaimer is on every page

    def test_the_address_itself_opens_the_site_for_visitors_and_the_panel_for_people_logged_in(self):
        self.assertRedirects(self.client.get("/", HTTP_ACCEPT_LANGUAGE="pt-BR,pt;q=0.9"), "/pt/", fetch_redirect_response=False)
        self.assertRedirects(self.client.get("/", HTTP_ACCEPT_LANGUAGE="de-DE"), "/en/", fetch_redirect_response=False)
        self.assertRedirects(self.client.get("/jobs/"), "/login/?next=/jobs/", fetch_redirect_response=False)
        user = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_the_old_terms_address_still_works(self):
        self.assertRedirects(self.client.get("/termos/"), "/pt/termos/", fetch_redirect_response=False)

    def test_while_not_indexable_nothing_is_offered_to_search_engines(self):
        robots = self.client.get("/robots.txt").content.decode()
        self.assertIn("Disallow: /", robots)
        self.assertNotIn("Allow:", robots)
        response = self.client.get("/pt/")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn('<meta name="robots" content="noindex, nofollow">', response.content.decode())

    @override_settings(SITE_INDEXABLE=True)
    def test_when_indexable_only_the_site_is_offered(self):
        robots = self.client.get("/robots.txt").content.decode()
        self.assertIn("Allow: /pt/", robots)
        self.assertIn("Allow: /en/", robots)
        self.assertIn("Sitemap: https://site.example/sitemap.xml", robots)
        site = self.client.get("/en/pricing/")
        self.assertFalse(site.has_header("X-Robots-Tag"))
        self.assertNotIn('name="robots"', site.content.decode())
        self.assertEqual(self.client.get("/login/")["X-Robots-Tag"], "noindex, nofollow")   # the panel never
        sitemap = self.client.get("/sitemap.xml").content.decode()
        self.assertEqual(sitemap.count("<loc>"), len(C.PAGES) * len(C.LANGS))
        self.assertIn('hreflang="x-default" href="https://site.example/en/faq/"', sitemap)

    def test_structured_data_and_previews(self):
        html = self.client.get("/pt/faq/").content.decode()
        self.assertIn('"@type": "FAQPage"', html)
        self.assertIn('"@type": "SoftwareApplication"', html)
        self.assertIn('<meta property="og:image" content="https://site.example/static/website/og.png">', html)

    def test_the_legal_pages_show_only_what_is_filled(self):
        html = self.client.get("/pt/termos/").content.decode()
        self.assertNotIn("oferecido por", html)
        self.assertNotIn("comarca", html)
        with self.settings(SITE_LEGAL=LEGAL):
            terms = self.client.get("/pt/termos/").content.decode()
            privacy = self.client.get("/en/privacy/").content.decode()
            contact = self.client.get("/pt/contato/").content.decode()
        self.assertIn("Empresa Exemplo", terms)
        self.assertIn("comarca de Cuiabá/MT", terms)
        self.assertIn("privacidade@example.com", privacy)
        self.assertIn("https://t.me/ikahub_suporte", contact)


@override_settings(SITE_URL="https://site.example", UMAMI_URL="", UMAMI_WEBSITE_ID="")
class InviteRequestTests(TestCase):
    def setUp(self):
        cache.clear()

    def _send(self, lang="pt", **data):
        url = {"pt": "/pt/contato/enviar/", "en": "/en/contact/enviar/"}[lang]
        return self.client.post(url, {"email": "amigo@example.com", "name": "Amigo", "accounts": "2-3", "message": "Oi", **data},
                                HTTP_X_REAL_IP="203.0.113.7")

    def test_a_request_is_kept_and_recorded(self):
        response = self._send()
        self.assertRedirects(response, "/pt/contato/?ok=1", fetch_redirect_response=False)
        item = InviteRequest.objects.get()
        self.assertEqual((item.email, item.accounts, item.language, item.ip), ("amigo@example.com", "2-3", "pt", "203.0.113.7"))
        self.assertTrue(AuditEvent.objects.filter(action="invite.requested").exists())
        self.assertIn("Pedido recebido", self.client.get("/pt/contato/?ok=1").content.decode())

    def test_robots_bad_emails_and_floods_are_refused(self):
        self._send(website="http://spam.example")                       # the hidden field
        self.assertEqual(InviteRequest.objects.count(), 0)
        self.assertEqual(self._send(lang="en", email="not-an-email").status_code, 400)
        for _ in range(5):
            self._send()
        response = self._send()
        self.assertEqual(response.status_code, 400)
        self.assertIn("Muitos pedidos", response.content.decode())
        self.assertEqual(InviteRequest.objects.count(), 5)

    def test_the_team_sees_the_requests_and_marks_them(self):
        self._send()
        item = InviteRequest.objects.get()
        staff = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        self.client.force_login(staff)
        page = self.client.get("/users/convites/").content.decode()
        self.assertIn("amigo@example.com", page)
        self.assertIn('value="amigo@example.com"', self.client.get("/users/convites/?email=amigo@example.com").content.decode())
        self.client.post("/users/convites/", {"handled": str(item.pk)})
        item.refresh_from_db()
        self.assertIsNotNone(item.handled_at)
        self.assertNotIn("Pedidos pelo site", self.client.get("/users/convites/").content.decode())

    def test_customers_never_see_the_requests(self):
        self._send()
        customer = get_user_model().objects.create_user("cliente", password="x")
        self.client.force_login(customer)
        self.assertEqual(self.client.get("/users/convites/").status_code, 404)


@override_settings(SITE_URL="https://site.example", UMAMI_URL="http://umami:3000", UMAMI_WEBSITE_ID="site-id")
class StatisticsTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_only_the_site_pages_carry_the_script(self):
        self.assertIn('data-website-id="site-id"', self.client.get("/pt/").content.decode())
        self.assertNotIn("stats/script.js", self.client.get("/login/").content.decode())

    @override_settings(UMAMI_URL="", UMAMI_WEBSITE_ID="")
    def test_without_umami_there_is_nothing(self):
        self.assertNotIn("stats/script.js", self.client.get("/pt/").content.decode())
        self.assertEqual(self.client.get("/stats/script.js").status_code, 404)
        self.assertEqual(self.client.post("/stats/api/send", "{}", content_type="application/json").status_code, 404)

    def test_the_count_goes_to_umami_only_for_this_site(self):
        with mock.patch("apps.website.views.requests.post") as post:
            post.return_value = mock.Mock(status_code=200, content=b"{}", headers={"Content-Type": "application/json"})
            other = self.client.post("/stats/api/send", '{"type": "event", "payload": {"website": "outro"}}',
                                     content_type="application/json")
            self.assertEqual(other.status_code, 400)
            post.assert_not_called()
            ok = self.client.post("/stats/api/send", '{"type": "event", "payload": {"website": "site-id", "url": "/pt/"}}',
                                  content_type="application/json", HTTP_X_REAL_IP="198.51.100.4", HTTP_USER_AGENT="Teste")
            self.assertEqual(ok.status_code, 200)
            headers = post.call_args.kwargs["headers"]
            self.assertEqual((headers["X-Forwarded-For"], headers["User-Agent"]), ("198.51.100.4", "Teste"))
            self.assertEqual(post.call_args.args[0], "http://umami:3000/api/send")

    def test_the_script_is_served_from_the_hub(self):
        with mock.patch("apps.website.views.requests.get") as get:
            get.return_value = mock.Mock(status_code=200, content=b"/* umami */", raise_for_status=lambda: None)
            response = self.client.get("/stats/script.js")
            self.client.get("/stats/script.js")
        self.assertEqual(response.content, b"/* umami */")
        self.assertEqual(get.call_count, 1)                              # kept for an hour
