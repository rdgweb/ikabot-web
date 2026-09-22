"""N-70: covers BuyAction._find_offer_in_listing, including the diagnostic logging
added when no offer matches (see game_client/actions/market.py)."""

import logging
import unittest

from game_client.actions.market import BuyAction

SAMPLE_LISTING = """
<table>
<tr>
  <td>
    <span class="short_text80">LandLumis</span><br>
    <span>(seller_player)</span>
    <a href="?view=takeOffer&destinationCityId=66593&oldView=branchOffice&activeTab=bargain&cityId=76660&position=10&type=444&resource=resource">Comprar</a>
  </td>
</tr>
<tr>
  <td>
    <span class="short_text80">OtherCity</span><br>
    <span>(other_player)</span>
    <a href="?view=takeOffer&destinationCityId=12345&oldView=branchOffice&activeTab=bargain&cityId=76660&position=10&type=444&resource=tradegood2">Comprar</a>
  </td>
</tr>
</table>
"""


class FindOfferInListingTests(unittest.TestCase):
    def setUp(self):
        self.action = BuyAction(client=None)

    def test_matches_offer_from_the_right_seller_and_resource(self):
        offer = self.action._find_offer_in_listing(SAMPLE_LISTING, seller_city_id=66593, resource_str="resource")

        self.assertIsNotNone(offer)
        self.assertEqual(offer["city_id"], 66593)
        self.assertEqual(offer["buyer_city_id"], 76660)
        self.assertEqual(offer["buyer_bo_pos"], 10)

    def test_ignores_offer_with_matching_city_but_different_resource(self):
        offer = self.action._find_offer_in_listing(SAMPLE_LISTING, seller_city_id=12345, resource_str="resource")

        self.assertIsNone(offer)

    def test_no_match_logs_diagnostic_with_what_was_actually_in_the_listing(self):
        """This is the N-70 diagnostic: distinguishes 'listing had other offers, just not
        this one' (logged here) from 'listing was empty' — needed to pin down why some
        purchases fail with 'Offer preview not found' even though the seller is in range."""
        with self.assertLogs("game_client.actions.market", level="WARNING") as captured:
            offer = self.action._find_offer_in_listing(SAMPLE_LISTING, seller_city_id=99999, resource_str="resource")

        self.assertIsNone(offer)
        self.assertEqual(len(captured.output), 1)
        self.assertIn("seller_city_id=99999", captured.output[0])
        self.assertIn("offers_seen=", captured.output[0])
        self.assertIn("66593", captured.output[0])  # the offers that WERE there, for diagnosis

    def test_empty_listing_still_logs_without_raising(self):
        with self.assertLogs("game_client.actions.market", level="WARNING") as captured:
            offer = self.action._find_offer_in_listing("<html></html>", seller_city_id=1, resource_str="resource")

        self.assertIsNone(offer)
        self.assertIn("offers_seen=[]", captured.output[0])


if __name__ == "__main__":
    unittest.main()
