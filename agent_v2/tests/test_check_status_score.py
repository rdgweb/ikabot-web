"""N-53: Check Status reads the account's official total score + rank from the
game's Pontuacao screen (view=highscore&showMe=1&ajax=1) and the
building/research/army breakdown from the island view (avatarScores)."""

import json
import unittest

from runners.check_status import _own_score_breakdown, _parse_own_highscore

# Own row as returned by s78-br for HAVIT (captured 2026-09-22).
_OWN_ROW = (
    '<tr class="alt own"> <td class="place bold">3.692 </td> <td class="name"> <div> '
    '<a href="?view=avatarProfile&avatarId=104404" title="HAVIT"> <span class=\'flag br\'>'
    '<span class=\'img\'></span><span class=\'avatarName\'>HAVIT</span></span> </a> </div> </td> '
    '<td class="allytag"> </td> <td class="score" title="135.555" >135.555 </td> '
    '<td class="action"></td> </tr>'
)
_OTHER_ROW = (
    '<tr class="alt"> <td class="place bold">3.691 </td> <td class="name">X</td> '
    '<td class="allytag">-CA-</td> <td class="score" title="135.600">135.600 </td> </tr>'
)


def _highscore_ajax(rows_html):
    html = f'<table class="table01">{rows_html}</table>'
    return json.dumps([
        ["updateGlobalData", {"headerData": {}}],
        ["changeView", ["highscore", html, {}]],
        ["updateTemplateData", {}],
    ])


class ParseOwnHighscoreTests(unittest.TestCase):
    def test_reads_official_total_and_rank_from_the_own_row(self):
        own = _parse_own_highscore(_highscore_ajax(_OTHER_ROW + _OWN_ROW))

        self.assertEqual(own, {"total": 135555, "place": 3692})

    def test_returns_none_without_an_own_row(self):
        self.assertIsNone(_parse_own_highscore(_highscore_ajax(_OTHER_ROW)))

    def test_returns_none_for_non_json_response(self):
        self.assertIsNone(_parse_own_highscore("<html>login</html>"))


class OwnScoreBreakdownTests(unittest.TestCase):
    def _island(self, scores=None):
        return {
            "cities": [{"id": "99", "owner_id": "777"}, {"id": "100", "owner_id": "555"}],
            "avatar_scores": scores if scores is not None else {
                "555": {"place": 3692, "building_score": 116472, "research_score": 11694, "army_score": 117},
                "777": {"place": 1, "building_score": 9, "research_score": 9, "army_score": 9},
            },
        }

    def test_picks_the_owner_of_the_given_city(self):
        self.assertEqual(
            _own_score_breakdown(self._island(), 100),
            {"building": 116472, "research": 11694, "army": 117},
        )

    def test_returns_none_when_city_is_not_on_the_island(self):
        self.assertIsNone(_own_score_breakdown(self._island(), "404"))

    def test_returns_none_when_owner_has_no_score_entry(self):
        self.assertIsNone(_own_score_breakdown(self._island(scores={}), "100"))


if __name__ == "__main__":
    unittest.main()
