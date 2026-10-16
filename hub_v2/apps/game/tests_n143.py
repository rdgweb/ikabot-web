"""N-143: Santuário dos Deuses — each city's god, whether it blesses anything, and the god it should worship."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.game.services.shrine import shrines_for_organization

SHRINE = {
    "city_name": "Valhala", "building_level": 27, "current_favor": 1275, "gods_worshipped": [2, 3, 6],
    "gods": {"2": {"grace_period": "2D 23h", "grace_seconds": 255600}, "3": {"grace_period": "21h 46m", "grace_seconds": 78360},
             "6": {"grace_period": "-", "grace_seconds": 0}},
    "worship_assignments": [
        {"slot": 1, "city_id": 1, "god_id": 3, "god_css": "god_tyche", "current_bonus": "57,00", "generated_resources": "667.489"},
        {"slot": 2, "city_id": 2, "god_id": 3, "god_css": "god_tyche", "current_bonus": "57,00", "generated_resources": "0"},
        {"slot": 3, "city_id": 3, "god_id": 6, "god_css": "god_hephaestos", "current_bonus": "0,00", "generated_resources": "0"},
    ],
}


class ShrinePanelTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        self.client.force_login(user)
        node = Node.objects.create(name="agente", transport="https")
        account = Account.objects.create(node=node, label="Conta", email="c@example.com", password_enc="x")
        ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br", server_language="br",
                                        server_number=78, name="Jogador")
        cities = [
            {"id": 1, "name": "Okolnir", "tradegood": 2, "shrine_state": SHRINE},          # marble + Tique: blessed
            {"id": 2, "name": "Valhala", "tradegood": 1},                                  # wine + Tique: nothing
            {"id": 3, "name": "Hell", "tradegood": 4},                                     # sulfur + Hefesto out of grace
            {"id": 4, "name": "Glitnir", "tradegood": 3},                                  # no god
        ]
        AccountSnapshot.objects.create(account=account, game_account=ga, military={}, base_snapshot={}, cities=cities)

    def test_each_city_says_whether_its_god_blesses_it(self):
        shrine = shrines_for_organization()[0]
        status = {c.name: (c.status, c.recommended) for c in shrine.cities}
        self.assertEqual(status, {"Okolnir": ("ok", 3), "Valhala": ("wrong_good", 2), "Hell": ("no_grace", 6), "Glitnir": ("no_god", 5)})
        self.assertEqual(shrine.bonus, "57,00")
        self.assertEqual([(g["name"], g["in_grace"]) for g in shrine.gods], [("Dionísio", True), ("Tique", True), ("Hefesto", False)])

    def test_the_page_shows_the_shrine(self):
        html = self.client.get("/game/temples/").content.decode()
        self.assertIn("Santuário dos Deuses", html)
        self.assertIn("não produz mármore", html)
        self.assertIn("sem graça", html)
        self.assertIn("game/gods/theia.png", html)          # Glitnir (crystal) should worship Teia
