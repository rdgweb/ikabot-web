"""A slow Docker Hub: a second, longer try; then the last known release (with a notice); an error only briefly."""
from unittest.mock import patch

import requests
from django.core.cache import cache
from django.test import TestCase

from . import services

PAYLOAD = {"results": [{"name": "0.4.52", "digest": "sha256:" + "a" * 64, "last_updated": "2026-11-21T22:00:00Z"}]}


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return PAYLOAD


class RegistryTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_a_slow_answer_gets_a_second_try(self):
        calls = []

        def get(*args, **kwargs):
            calls.append(kwargs["timeout"])
            if len(calls) == 1:
                raise requests.ReadTimeout("lento")
            return _Response()

        with patch("apps.notes.services.requests.get", side_effect=get):
            release = services.get_registry_release("hub", force=True)
        self.assertEqual((release.version, release.error, calls), ("0.4.52", "", [5, 12]))

    def test_down_shows_the_last_known_release_and_retries_soon(self):
        with patch("apps.notes.services.requests.get", return_value=_Response()):
            services.get_registry_release("agent", force=True)
        with patch("apps.notes.services.requests.get", side_effect=requests.ReadTimeout("fora")):
            release = services.get_registry_release("agent", force=True)
        self.assertEqual((release.version, release.error), ("0.4.52", ""))
        self.assertTrue(release.notice)
        self.assertTrue(release.digest.startswith("sha256:"))               # the platform agent can still be created
        cache.delete(services._release_cache_key("agent") + ":last_good")
        with patch("apps.notes.services.requests.get", side_effect=requests.ReadTimeout("fora")):
            release = services.get_registry_release("agent", force=True)
        self.assertIn("indisponível", release.error)
