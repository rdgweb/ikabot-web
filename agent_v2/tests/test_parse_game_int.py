"""N-40: single, shared numeric parser (previously triplicated in check_status.py,
resource_transport.py and wine_tavern.py) that tolerates unicode thousand spaces."""

import unittest

from game_client.parsers.numbers import parse_game_float, parse_game_int


class ParseGameIntTests(unittest.TestCase):
    def test_plain_int_and_float_pass_through(self):
        self.assertEqual(parse_game_int(1234), 1234)
        self.assertEqual(parse_game_int(1234.9), 1234)

    def test_none_and_blank_use_the_default(self):
        self.assertEqual(parse_game_int(None), 0)
        self.assertEqual(parse_game_int(""), 0)
        self.assertEqual(parse_game_int("   "), 0)
        self.assertEqual(parse_game_int("no digits here"), 0)
        self.assertEqual(parse_game_int(None, default=-1), -1)

    def test_dot_as_thousands_separator(self):
        self.assertEqual(parse_game_int("1.139"), 1139)

    def test_comma_as_thousands_separator(self):
        self.assertEqual(parse_game_int("1,139"), 1139)

    def test_comma_as_decimal_separator_when_not_three_digit_group(self):
        self.assertEqual(parse_game_int("1,5"), 1)  # truncates, like int(1.5)

    def test_dot_thousands_with_comma_decimal(self):
        self.assertEqual(parse_game_int("1.139,50"), 1139)

    def test_comma_thousands_with_dot_decimal(self):
        self.assertEqual(parse_game_int("1,139.50"), 1139)

    def test_negative_numbers(self):
        self.assertEqual(parse_game_int("-42"), -42)

    def test_extracts_the_numeric_token_from_surrounding_text(self):
        self.assertEqual(parse_game_int("Gold: 5000 gp"), 5000)

    # --- N-40's actual bug: unicode/regular thousand spaces silently truncated ---

    def test_regular_space_as_thousands_separator(self):
        self.assertEqual(parse_game_int("1 234"), 1234)

    def test_nbsp_as_thousands_separator(self):
        self.assertEqual(parse_game_int("1 234"), 1234)

    def test_narrow_nbsp_as_thousands_separator_french_locale(self):
        self.assertEqual(parse_game_int("1 234 567"), 1234567)

    def test_space_thousands_combined_with_comma_decimal(self):
        self.assertEqual(parse_game_int("1 234,50"), 1234)


class ParseGameFloatTests(unittest.TestCase):
    def test_keeps_the_decimal_part(self):
        self.assertEqual(parse_game_float("1.139,50"), 1139.5)
        self.assertEqual(parse_game_float(3.5), 3.5)
        self.assertEqual(parse_game_float(None), 0.0)
        self.assertEqual(parse_game_float("1 234,5"), 1234.5)


class SharedModuleNotReDuplicatedTests(unittest.TestCase):
    """N-40: guard against the helper drifting back into three separate copies."""

    def test_resource_transport_imports_the_shared_function(self):
        from services import resource_transport

        self.assertIs(resource_transport._num, parse_game_int)

    def test_wine_tavern_imports_the_shared_function(self):
        from services import wine_tavern

        self.assertIs(wine_tavern._num, parse_game_int)


if __name__ == "__main__":
    unittest.main()
