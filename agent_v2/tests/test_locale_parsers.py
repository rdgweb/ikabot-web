"""N-41: three locale-dependent parsing bugs, each dropping/zeroing real data instead
of erroring — combat losses (pt-BR "." thousands), market offers (en-locale "," "
thousands), spy agent counts (any locale, count >= 1000). All now go through the
shared game_client.parsers.numbers helper (N-40)."""

import unittest
from unittest.mock import MagicMock

from game_client.actions.market import GetOffersAction
from game_client.actions.military import FetchCombatDetailedReportAction
from game_client.actions.spy import SpyReportsAction


def _pad(html: str) -> str:
    """Ensure the fragment clears the real parser's len(item) > 200 gate."""
    return html + "<!-- " + ("x" * 250) + " -->"


class CombatReportLossParsingTests(unittest.TestCase):
    """Regex used to require a comma-only class ([\\d,]+): a "." thousands
    separator (pt-BR servers) made the WHOLE slot fail to match, so the loss for
    that unit silently vanished from the report instead of being misparsed."""

    def _run(self, html: str) -> dict:
        client = MagicMock()
        first_page = _pad(
            'battlefield Round<br/>1/1 '
            'id="slot11_0_0" class="slot army_small normal s101"><div class="number center">' + html
        )
        client._request.return_value.json.side_effect = [
            [["changeHTML", [first_page]]],
        ]
        action = FetchCombatDetailedReportAction(client=client)
        return action.execute(city_id=1, combat_id=1, max_rounds=1)

    def test_dot_as_thousands_separator_in_losses_is_not_dropped(self):
        result = self._run("5.000 (-1.234)")

        self.assertEqual(result["attacker_losses"], {"101": 1234})

    def test_comma_as_thousands_separator_in_losses_is_not_dropped(self):
        result = self._run("5,000 (-1,234)")

        self.assertEqual(result["attacker_losses"], {"101": 1234})

    def test_plain_number_still_works(self):
        result = self._run("50 (-7)")

        self.assertEqual(result["attacker_losses"], {"101": 7})


class MarketOfferParsingTests(unittest.TestCase):
    """Amount/price char class used to omit "," — an en-locale offer (comma
    thousands, e.g. "1,234") failed to parse and the whole offer was dropped."""

    def _offers(self, amount_text: str, price_text: str) -> list[dict]:
        action = GetOffersAction(client=None)
        html = (
            f'<td class="amount">{amount_text}</td>'
            f'<td class="price">{price_text}</td>'
            'href="?view=takeOffer&destinationCityId=555&oldView=branchOffice'
            '&cityId=1&position=1&type=444&resource=resource"'
        )
        return action._parse_all_offers(html, "resource")

    def test_comma_thousands_offer_is_not_dropped(self):
        offers = self._offers("1,234", "50")

        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["seller_city_id"], 555)
        self.assertEqual(offers[0]["amount"], 1234)
        self.assertEqual(offers[0]["price_per_unit"], 50)

    def test_dot_thousands_offer_still_works(self):
        offers = self._offers("1.234", "50")

        self.assertEqual(offers[0]["amount"], 1234)


class SpyAgentCountParsingTests(unittest.TestCase):
    """.isdigit() ran on the string BEFORE separators were stripped, so any count
    >= 1000 (which necessarily contains a separator) always read as 0."""

    def _lost_and_sent(self, text: str) -> tuple[int, int]:
        action = SpyReportsAction(client=None)
        html = f'id="message1" class="row"><div class="lostAgents">{text}</div></table>'
        reports = action._parse_reports(html)
        self.assertEqual(len(reports), 1)
        return reports[0]["agents_lost"], reports[0]["agents_sent"]

    def test_four_digit_counts_are_no_longer_zeroed(self):
        self.assertEqual(self._lost_and_sent("1.234 / 2.000"), (1234, 2000))

    def test_comma_thousands_counts_are_no_longer_zeroed(self):
        self.assertEqual(self._lost_and_sent("1,234 / 2,000"), (1234, 2000))

    def test_small_counts_still_work(self):
        self.assertEqual(self._lost_and_sent("3 / 5"), (3, 5))


if __name__ == "__main__":
    unittest.main()
