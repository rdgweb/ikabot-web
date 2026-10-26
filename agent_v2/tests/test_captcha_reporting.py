"""N-164: the piracy flow tells the hub whether the game took the captcha answer, and counts the ones that did
not block the mission."""
import unittest
from unittest import mock

from tests.test_piracy_captcha import _Client, _Hub, _Session, _capture_response, piracy_actions


class _ReportingHub(_Hub):
    def __init__(self, solutions):
        super().__init__(solutions)
        self.results = []
        self.seen = []

    def create_captcha_challenge(self, captcha_type, image_b64, game_account_id=""):
        result = super().create_captcha_challenge(captcha_type, image_b64, game_account_id)
        return {**result, "challenge_id": 70 + len(self.images)}

    def report_captcha_result(self, challenge_id, accepted):
        self.results.append((challenge_id, accepted))

    def report_captcha_seen(self, captcha_type, game_account_id=""):
        self.seen.append((captcha_type, game_account_id))


@mock.patch.object(piracy_actions.time, "sleep", lambda *_a, **_k: None)
class CaptchaReportingTests(unittest.TestCase):
    def _run(self, posts, solutions):
        hub = _ReportingHub(solutions)
        try:
            piracy_actions.PiracyMissionAction(_Client(_Session(posts), hub)).execute(39269, 9, game_account_id="ga-1")
        except Exception:
            pass
        return hub

    def test_an_accepted_answer_is_reported_as_accepted(self):
        hub = self._run([_capture_response(ship_in_port=True, captcha=True),
                         _capture_response(ship_in_port=False, captcha=False, time_remaining=1800)], ["abcde"])
        self.assertEqual(hub.results, [(71, True)])

    def test_a_refused_answer_is_reported_as_refused(self):
        hub = self._run([_capture_response(ship_in_port=True, captcha=True),
                         _capture_response(ship_in_port=True, captcha=True),       # captcha again: wrong answer
                         _capture_response(ship_in_port=False, captcha=False, time_remaining=1800)], ["wrong", "right"])
        self.assertEqual(hub.results[0], (71, False))
        self.assertEqual(hub.results[-1], (72, True))

    def test_a_captcha_that_did_not_block_is_counted(self):
        hub = self._run([_capture_response(ship_in_port=False, captcha=True, time_remaining=900)], [])
        self.assertEqual(hub.seen, [("pirate", "ga-1")])
        self.assertEqual(hub.results, [])


if __name__ == "__main__":
    unittest.main()
