"""N-241: a world is shown by its official name with its code ("Gigas · s78-br"); the code alone when unknown."""
from unittest import mock

from django.core.cache import cache
from django.template import Context, Template
from django.test import SimpleTestCase, override_settings

from apps.accounts import world_names

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "n241"}}
LOBBY = [
    {"language": "br", "number": 78, "name": "Gigas"},
    {"language": "br", "number": 79, "name": "Ikaros"},
    {"language": "de", "number": "x", "name": "bad"},
]


@override_settings(CACHES=LOCMEM)
class WorldNameTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def _read(self, payload=LOBBY, error=None):
        response = mock.Mock(json=mock.Mock(return_value=payload), raise_for_status=mock.Mock(side_effect=error))
        with mock.patch.object(world_names.requests, "get", return_value=response):
            return world_names.refresh_world_names()

    def test_names_come_from_the_lobby_list(self):
        self.assertEqual(self._read(), {"s78-br": "Gigas", "s79-br": "Ikaros"})
        render = Template("{{ a|world }} | {{ b|world }} | {{ a|world_name }}").render(
            Context({"a": "s78-br", "b": "s99-br"}))
        self.assertEqual(render, "Gigas · s78-br | s99-br | Gigas")

    def test_a_failed_read_keeps_the_last_good_names(self):
        self._read()
        cache.delete(world_names.CACHE_KEY)
        self.assertEqual(self._read(error=world_names.requests.RequestException("down")), {"s78-br": "Gigas", "s79-br": "Ikaros"})
        self.assertEqual(world_names.world_label("s78-br"), "Gigas · s78-br")

    def test_a_page_never_waits_for_the_lobby(self):
        with mock.patch.object(world_names.requests, "get") as get:
            self.assertEqual(world_names.world_label("s78-br"), "s78-br")
            self.assertEqual(world_names.world_label(""), "")
            get.assert_not_called()
