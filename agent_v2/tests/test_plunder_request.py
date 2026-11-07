"""The land plunder sends what the game accepts: the source city, ajax headers and the newest actionRequest (a stale
one is answered with "Acesso restrito")."""
import unittest

from game_client.actions.military import PlunderLandAction


class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class _FakeClient:
    _server_url = "https://s78-br.ikariam.gameforge.com/index.php"

    def __init__(self):
        self._action_request = "t0"
        self.calls = []
        self.session = None          # change_current_city fails quietly without a session
        self._replies = [
            [["updateGlobalData", {"actionRequest": "t1"}]],                                  # island view
            [["updateGlobalData", {"actionRequest": "t2"}],
             ["changeView", ["plunder", "<script>new missionController(40, 500, 12360, 1)</script>" + " " * 60]]],
            [["updateGlobalData", {"actionRequest": "t3"}], ["provideFeedback", [{"type": 10, "text": "ok"}]]],
        ]

    def _request(self, method, url, **kwargs):
        self.calls.append((method, dict(kwargs.get("params") or kwargs.get("data") or {}), kwargs.get("headers")))
        return _Resp(self._replies.pop(0))


class PlunderRequestTests(unittest.TestCase):
    def test_source_city_headers_and_fresh_token(self):
        client = _FakeClient()
        result = PlunderLandAction(client).execute(from_city_id=37457, to_city_id=45240, island_id=1909,
                                                    units={303: 30}, transporters=40)
        (_, island, island_headers), (_, view, _), (_, send, send_headers) = client.calls
        self.assertEqual(view["actionRequest"], "t1")          # the island view's token, not the stale one
        self.assertEqual(send["actionRequest"], "t2")
        self.assertEqual((send["cityId"], send["currentCityId"]), (37457, 37457))
        self.assertTrue(send_headers and island_headers)
        self.assertEqual(result["travel_seconds"], 12360)
        self.assertEqual(client._action_request, "t3")


if __name__ == "__main__":
    unittest.main()
