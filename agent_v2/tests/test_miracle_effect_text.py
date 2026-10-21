"""N-160: the miracle effect is logged as text, also when the snapshot kept the dict's text."""
import unittest

from runners.economy import _plain_text


class MiracleEffectTextTests(unittest.TestCase):
    def test_dict_and_its_text_become_plain_text(self):
        self.assertEqual(_plain_text({"text": "Aumenta em 30%"}), "Aumenta em 30%")
        self.assertEqual(_plain_text("{'text': 'Aumenta em 30%'}"), "Aumenta em 30%")
        self.assertEqual(_plain_text("Aumenta em 30%"), "Aumenta em 30%")


if __name__ == "__main__":
    unittest.main()
