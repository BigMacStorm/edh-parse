import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.stock import StockCollection
from edhparse.parsing import parse_card_counts_lines, parse_card_names_lines
from edhparse.services.find_in_binders import (
    FIND_BINDERS,
    BinderHit,
    find_cards_in_binders,
    group_hits_by_rarity_and_color,
    run_find_in_binders,
)


class TestParseCardNamesLines(unittest.TestCase):
    def test_mtgo_counts_and_blanks(self):
        lines = ["1 Sol Ring", "", "Lightning Bolt", "2 Counterspell"]
        self.assertEqual(
            parse_card_names_lines(lines),
            ["Sol Ring", "Lightning Bolt", "Counterspell"],
        )
        self.assertEqual(
            parse_card_counts_lines(lines),
            {"Sol Ring": 1, "Lightning Bolt": 1, "Counterspell": 2},
        )


class TestFindInBinders(unittest.TestCase):
    def _write_stock(self, rows):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8")
        writer = csv.writer(f)
        writer.writerow(["Name", "Binder Name", "Quantity"])
        for row in rows:
            writer.writerow(row)
        f.close()
        return f.name

    def test_default_binders_match_stock_csv(self):
        self.assertEqual(FIND_BINDERS, ("stock", "binder", "secret binder"))

    def test_find_only_selected_binders(self):
        path = self._write_stock(
            [
                ["Sol Ring", "Binder", "2"],
                ["Sol Ring", "Draft", "5"],
                ["Lightning Bolt", "Stock", "1"],
            ]
        )
        try:
            stock = StockCollection.load(path, FIND_BINDERS)
            with patch("edhparse.services.find_in_binders.scryfall_bulk.get_card_by_name_or_face") as mock_card:
                mock_card.side_effect = lambda name: {
                    "Sol Ring": {"rarity": "uncommon", "color_identity": []},
                    "Lightning Bolt": {"rarity": "common", "color_identity": ["R"]},
                }.get(name)
                hits = find_cards_in_binders({"Sol Ring": 3, "Lightning Bolt": 1}, stock)
            sol = hits[0]
            self.assertEqual(sol.name, "Sol Ring")
            self.assertEqual(sol.needed, 3)
            self.assertEqual(sol.owned, 2)
            self.assertEqual(dict(sol.binders), {"Binder": 2})
            bolt = hits[1]
            self.assertEqual(bolt.name, "Lightning Bolt")
            self.assertEqual(bolt.color_bucket, "Red")
            self.assertEqual(dict(bolt.binders), {"Stock": 1})
        finally:
            Path(path).unlink(missing_ok=True)

    def test_dfc_face_name_resolves_to_owned_full_name(self):
        path = self._write_stock(
            [["Legion Leadership // Legion Stronghold", "Binder", "1"]]
        )
        try:
            stock = StockCollection.load(path, FIND_BINDERS)
            with patch("edhparse.services.find_in_binders.scryfall_bulk.get_card_by_name_or_face") as mock_card:
                mock_card.side_effect = lambda name: {
                    "Legion Leadership // Legion Stronghold": {
                        "rarity": "rare",
                        "color_identity": ["R", "W"],
                        "card_faces": [
                            {"name": "Legion Leadership"},
                            {"name": "Legion Stronghold"},
                        ],
                    },
                    "Legion Leadership": {
                        "rarity": "rare",
                        "color_identity": ["R", "W"],
                        "card_faces": [
                            {"name": "Legion Leadership"},
                            {"name": "Legion Stronghold"},
                        ],
                    },
                }.get(name)
                hits = find_cards_in_binders({"Legion Leadership": 1}, stock)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].owned, 1)
            self.assertEqual(dict(hits[0].binders), {"Binder": 1})
        finally:
            Path(path).unlink(missing_ok=True)

    def test_group_by_rarity_and_color(self):
        hits = [
            BinderHit(name="Koma, World-Eater", needed=1, owned=2, binders=[("Binder", 2)], rarity_bucket="Rares", color_bucket="Multicolor"),
            BinderHit(name="Sol Ring", needed=2, owned=5, binders=[("Stock", 5)], rarity_bucket="Commons", color_bucket="Colorless"),
            BinderHit(name="Missing", needed=1, owned=0, binders=[], rarity_bucket="Uncommons", color_bucket="Green"),
        ]
        groups, missing = group_hits_by_rarity_and_color(hits)
        self.assertEqual([h.name for h in missing], ["Missing"])
        self.assertEqual(groups["Rares"]["Multicolor"][0].name, "Koma, World-Eater")
        self.assertEqual(groups["Commons"]["Colorless"][0].needed, 2)

    def test_run_writes_grouped_html(self):
        path = self._write_stock(
            [
                ["Sol Ring", "Stock", "5"],
                ["Koma, World-Eater", "Binder", "2"],
                ["Lightning Bolt", "Stock", "1"],
            ]
        )
        out = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
        out.close()
        try:
            with patch("edhparse.services.find_in_binders.create_cached_session"), patch(
                "edhparse.services.find_in_binders.scryfall_bulk.ensure_loaded"
            ), patch("edhparse.services.find_in_binders.scryfall_bulk.get_card_by_name_or_face") as mock_card:
                mock_card.side_effect = lambda name: {
                    "Sol Ring": {"rarity": "common", "color_identity": []},
                    "Koma, World-Eater": {"rarity": "rare", "color_identity": ["G", "U"]},
                    "Lightning Bolt": {"rarity": "common", "color_identity": ["R"]},
                }.get(name)
                result = run_find_in_binders(
                    card_list_text="1 Sol Ring\n1 Koma, World-Eater\n1 Sol Ring\nNot A Card",
                    stock_csv=path,
                    out_path=out.name,
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["found"], 2)
            self.assertEqual(result["missing"], 1)
            html = Path(out.name).read_text(encoding="utf-8")
            self.assertIn("Rares", html)
            self.assertIn("Commons", html)
            self.assertIn("Multicolor", html)
            self.assertIn("Colorless", html)
            self.assertIn("Need", html)
            self.assertIn("Have", html)
            self.assertIn("2</td><td class='num'>5", html)
            self.assertIn("Binder", html)
            self.assertIn("Stock", html)
            self.assertIn("Sol Ring", html)
            self.assertIn("Not A Card", html)
            self.assertIn("Binders searched:", html)
        finally:
            Path(path).unlink(missing_ok=True)
            Path(out.name).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
