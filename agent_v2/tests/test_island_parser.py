import json
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("island_actions_under_test", ROOT / "game_client" / "actions" / "island.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
_parse_island_html = MODULE._parse_island_html


class IslandParserTests(unittest.TestCase):
    def test_preserves_actions_dict_from_city_payload(self):
        island_payload = [
            [0, 0],
            [0, {
                "id": "4478",
                "name": "Kelaitia",
                "xCoord": "84",
                "yCoord": "89",
                "tradegood": 1,
                "resourceLevel": "29",
                "tradegoodLevel": "25",
                "wonder": "8",
                "wonderName": "Colosso",
                "wonderLevel": "5",
                "cities": [
                    {
                        "type": "city",
                        "name": "Tundra",
                        "id": 55855,
                        "level": 24,
                        "ownerId": "106049",
                        "ownerName": "Pradus",
                        "ownerAllyId": 0,
                        "actions": {"piracy_raid": 1},
                        "state": "",
                        "viewAble": 0,
                        "infestedByPlague": False,
                    }
                ],
            }],
        ]
        html = f"ajax.Responder, {json.dumps(island_payload)})"

        parsed = _parse_island_html(html)

        self.assertEqual(parsed["island_id"], "4478")
        self.assertEqual(parsed["cities"][0]["id"], "55855")
        self.assertEqual(parsed["cities"][0]["actions"], {"piracy_raid": 1})


if __name__ == "__main__":
    unittest.main()
