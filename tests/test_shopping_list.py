import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.stock import StockCollection
from edhparse.services.shopping_list import (
    allocate_ownership,
    is_basic_land_name,
    manapool_paste_text,
    merge_shopping_list,
    run_shopping_list,
    ShoppingCardRow,
)
from core.config import RunConfig


class TestShoppingListCore(unittest.TestCase):
    def test_merge_and_basic_filter(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("1 Sol Ring\n1 Island\n1 Sol Ring\n2 Mountain\n1 Counterspell\n")
            path = f.name
        try:
            merged = merge_shopping_list(path)
            self.assertEqual(merged["Sol Ring"], 2)
            self.assertEqual(merged["Island"], 1)
            self.assertEqual(merged["Counterspell"], 1)
            self.assertTrue(is_basic_land_name("Island"))
            self.assertTrue(is_basic_land_name("Snow-Covered Forest"))
            self.assertFalse(is_basic_land_name("Breeding Pool"))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_partial_ownership_allocation(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Name", "Binder Name", "Quantity"])
            writer.writerow(["Sol Ring", "Binder", "1"])
            writer.writerow(["Sol Ring", "Stock", "1"])
            writer.writerow(["Counterspell", "secret binder", "1"])
            path = f.name
        try:
            stock = StockCollection.load(path, ["Binder", "Stock", "secret binder"])
            name_to_canonical = {
                "Sol Ring": "Sol Ring",
                "Counterspell": "Counterspell",
                "Lightning Bolt": "Lightning Bolt",
            }
            needed = {"Sol Ring": 3, "Counterspell": 1, "Lightning Bolt": 2}
            owned, by_binder = allocate_ownership(needed, stock, name_to_canonical)
            self.assertEqual(owned["Sol Ring"], 2)
            self.assertEqual(owned["Counterspell"], 1)
            self.assertNotIn("Lightning Bolt", owned)
            self.assertEqual(by_binder["Binder"].get("Sol Ring"), 1)
            self.assertEqual(by_binder["Stock"].get("Sol Ring"), 1)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_manapool_paste_text(self):
        rows = [
            ShoppingCardRow("Sol Ring", 2, 1, 1),
            ShoppingCardRow("Counterspell", 1, 0, 1),
            ShoppingCardRow("Lightning Bolt", 1, 1, 0),
        ]
        text = manapool_paste_text(rows)
        self.assertEqual(text, "1 Counterspell\n1 Sol Ring\n")

    def test_dfc_front_face_matches_full_stock_name(self):
        """List has front face; binder has Front // Back — must count as owned."""
        with tempfile.TemporaryDirectory() as tmp:
            list_path = Path(tmp) / "list.txt"
            list_path.write_text(
                "1 Ojer Taq, Deepest Foundation\n",
                encoding="utf-8",
            )
            stock_path = Path(tmp) / "stock.csv"
            with open(stock_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Name", "Binder Name", "Quantity"])
                writer.writerow(
                    [
                        "Ojer Taq, Deepest Foundation // Temple of Civilization",
                        "Binder",
                        "1",
                    ]
                )

            out = Path(tmp) / "out.html"

            with patch(
                "edhparse.services.shopping_list.scryfall_bulk.get_card_by_name_or_face",
                return_value=None,
            ), patch(
                "edhparse.services.shopping_list.ensure_scryfall_bulk",
                return_value=True,
            ):
                result = run_shopping_list(
                    list_path,
                    stock_path,
                    RunConfig(),
                    binders=["Binder", "Stock", "secret binder"],
                    out_path=str(out),
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result.get("buy_count"), 0)
            self.assertEqual(result.get("owned_count"), 1)
            paste = out.with_name("out_manapool.txt").read_text(encoding="utf-8")
            self.assertEqual(paste.strip(), "")

    def test_run_shopping_list_mocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            list_path = Path(tmp) / "list.txt"
            list_path.write_text(
                "1 Sol Ring\n1 Sol Ring\n1 Island\n1 Counterspell\n",
                encoding="utf-8",
            )
            stock_path = Path(tmp) / "stock.csv"
            with open(stock_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Name", "Binder Name", "Quantity"])
                writer.writerow(["Sol Ring", "Binder", "1"])

            out = Path(tmp) / "out.html"

            def fake_bulk(name):
                mapping = {
                    "Sol Ring": {"name": "Sol Ring", "type_line": "Artifact"},
                    "Island": {"name": "Island", "type_line": "Basic Land — Island"},
                    "Counterspell": {"name": "Counterspell", "type_line": "Instant"},
                }
                return mapping.get(name)

            with patch(
                "edhparse.services.shopping_list.scryfall_bulk.get_card_by_name_or_face",
                side_effect=fake_bulk,
            ), patch(
                "edhparse.services.shopping_list.ensure_scryfall_bulk",
                return_value=True,
            ):
                result = run_shopping_list(
                    list_path,
                    stock_path,
                    RunConfig(),
                    binders=["Binder", "Stock", "secret binder"],
                    out_path=str(out),
                )

            self.assertTrue(result["ok"])
            self.assertTrue(out.is_file())
            txt = out.with_name("out_manapool.txt")
            self.assertTrue(txt.is_file())
            paste = txt.read_text(encoding="utf-8")
            self.assertIn("1 Counterspell", paste)
            self.assertIn("1 Sol Ring", paste)
            self.assertNotIn("Island", paste)

            html = out.read_text(encoding="utf-8")
            self.assertIn("Still need to buy", html)
            self.assertIn("ManaPool paste list", html)
            self.assertIn("Copy list", html)
            self.assertIn("Basic lands removed", html)


if __name__ == "__main__":
    unittest.main()
