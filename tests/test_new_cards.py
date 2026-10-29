import unittest
from unittest.mock import MagicMock, patch

from clients import scryfall_bulk
from edhparse.services.new_cards import _annotate_first_printing, _is_mechanically_new


class TestScryfallBulkNameMatch(unittest.TestCase):
    def test_prefers_real_card_over_jumpstart_theme(self):
        land = {
            "name": "Savage Lands",
            "type_line": "Land",
            "set": "msc",
            "released_at": "2026-06-26",
            "reprint": True,
            "layout": "normal",
        }
        theme = {
            "name": "Savage Lands",
            "type_line": "Card",
            "set": "fmsc",
            "released_at": "2026-06-26",
            "reprint": False,
            "layout": "normal",
        }
        picked = scryfall_bulk._pick_best_name_match([theme, land], "Savage Lands")
        self.assertEqual(picked["type_line"], "Land")


class TestMechanicallyNew(unittest.TestCase):
    def test_old_card_is_not_mechanically_new(self):
        card = MagicMock()
        card.first_print_released_at = "2012-11-02"
        self.assertFalse(_is_mechanically_new(card, "2025-02-21"))

    def test_recent_debut_is_mechanically_new(self):
        card = MagicMock()
        card.first_print_released_at = "2025-11-08"
        self.assertTrue(_is_mechanically_new(card, "2025-02-21"))


class TestAnnotateFirstPrintingFallback(unittest.TestCase):
    def test_uses_printing_when_debut_index_misses(self):
        card = MagicMock()
        card.oracle_id = "missing-from-debut-index"
        card.is_reprint = False
        card.released_at = "2026-10-02"
        card.set_code = "fra"
        with patch(
            "edhparse.services.new_cards.scryfall_bulk.get_debut_printing",
            return_value=(None, None),
        ):
            _annotate_first_printing({"Master of Barbs": card})
        self.assertEqual(card.first_print_released_at, "2026-10-02")
        self.assertEqual(card.first_print_set_code, "fra")


if __name__ == "__main__":
    unittest.main()
