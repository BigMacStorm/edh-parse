import csv
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from edhparse.cli import (
    _load_owned_quantities_by_binder_from_stock_csv,
    _load_owned_quantities_from_stock_csv,
    _print_convert_need_to_buy,
    run_convert_deck,
)


class TestConvertDeck(unittest.TestCase):

    def test_load_owned_quantities_by_binder_from_stock_csv(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Name", "Binder Name", "Quantity"])
            writer.writerow(["Sol Ring", "Binder", "2"])
            writer.writerow(["Lightning Bolt", "Stock", "1"])
            writer.writerow(["Counterspell", "Other", "4"])
            path = f.name

        try:
            binder_order, by_binder = _load_owned_quantities_by_binder_from_stock_csv(
                path, ["Stock", "Binder"]
            )
            self.assertEqual(binder_order, ["Stock", "Binder"])
            self.assertEqual(dict(by_binder["Binder"]), {"Sol Ring": 2})
            self.assertEqual(dict(by_binder["Stock"]), {"Lightning Bolt": 1})
        finally:
            Path(path).unlink(missing_ok=True)

    def test_load_owned_quantities_from_stock_csv(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Name", "Binder Name", "Quantity"])
            writer.writerow(["Sol Ring", "Binder", "2"])
            writer.writerow(["Lightning Bolt", "Stock", "1"])
            writer.writerow(["Counterspell", "Other", "4"])
            path = f.name

        try:
            quantities = _load_owned_quantities_from_stock_csv(path, ["Binder", "stock"])
            self.assertEqual(quantities, {"Sol Ring": 2, "Lightning Bolt": 1})
        finally:
            Path(path).unlink(missing_ok=True)

    def test_print_convert_need_to_buy_aligns_prices(self):
        buffer = io.StringIO()
        to_buy = {
            "Cheap Card": {"quantity": 1, "price": 12.34},
            "Expensive Card": {"quantity": 1, "price": 123.4},
        }
        with redirect_stdout(buffer):
            _print_convert_need_to_buy(to_buy, "price")
        output = buffer.getvalue()
        self.assertIn(" 12.34  -  Cheap Card", output)
        self.assertIn("123.40  -  Expensive Card", output)
        self.assertNotIn("1x ", output)

    def test_print_convert_need_to_buy_sorts_by_name(self):
        buffer = io.StringIO()
        to_buy = {
            "Zebra Card": {"quantity": 1, "price": 50.0},
            "Alpha Card": {"quantity": 1, "price": 1.0},
        }
        with redirect_stdout(buffer):
            _print_convert_need_to_buy(to_buy, "name")
        lines = [line for line in buffer.getvalue().splitlines() if " - " in line]
        self.assertEqual(lines[0], " 1.00  -  Alpha Card")
        self.assertEqual(lines[1], "50.00  -  Zebra Card")

    def test_print_convert_deck_changes_sorts_by_name(self):
        buffer = io.StringIO()
        added = {"Zebra Card": 1, "Alpha Card": 1}
        removed = {"Mogis, God of Slaughter": 1, "Arena of Glory": 1}
        prices = {"Zebra Card": 50.0, "Alpha Card": 1.0, "Mogis, God of Slaughter": 2.0, "Arena of Glory": 1.0}
        with redirect_stdout(buffer):
            from edhparse.cli import _print_convert_deck_changes
            _print_convert_deck_changes(added, removed, "name", prices)
        output = buffer.getvalue()
        added_block = output.split("Cards removed:")[0]
        removed_block = output.split("Cards removed:")[1]
        self.assertLess(added_block.index("Alpha Card"), added_block.index("Zebra Card"))
        self.assertLess(removed_block.index("Arena of Glory"), removed_block.index("Mogis, God of Slaughter"))
        self.assertIn("  1.00  -  Alpha Card", output)
        self.assertIn("Subtotal:", output)

    def test_run_convert_deck_counts_current_deck_as_owned(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_deck = tmp_path / "old.txt"
            new_deck = tmp_path / "new.txt"
            stock_csv = tmp_path / "stock.csv"

            old_deck.write_text("1 Ardenn, Intrepid Archaeologist\n", encoding="utf-8")
            new_deck.write_text("1 Ardenn, Intrepid Archaeologist\n1 Lightning Bolt\n", encoding="utf-8")

            with open(stock_csv, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Name", "Binder Name", "Quantity"])

            args = type(
                "Args",
                (),
                {
                    "convert_deck": [str(old_deck), str(new_deck)],
                    "convert_stock_csv": str(stock_csv),
                    "convert_binders": ["Binder"],
                    "convert_sort": "price",
                    "fresh_cache": False,
                },
            )()

            def fake_bulk_lookup(name):
                prices = {"usd": "1.00"}
                if name == "Ardenn, Intrepid Archaeologist":
                    return {"name": name, "prices": prices, "type_line": "Legendary Creature"}
                if name == "Lightning Bolt":
                    return {"name": name, "prices": prices, "type_line": "Instant"}
                return None

            with patch("edhparse.services.convert.scryfall_bulk.ensure_loaded"):
                with patch(
                    "edhparse.services.convert.scryfall_bulk.get_card_by_name_or_face",
                    side_effect=fake_bulk_lookup,
                ):
                    with patch("builtins.print") as mock_print:
                        run_convert_deck(args)

            printed = "\n".join(str(c.args[0]) for c in mock_print.call_args_list if c.args)
            self.assertIn("Current deck:", printed)
            self.assertIn("Ardenn, Intrepid Archaeologist", printed)
            self.assertIn("Lightning Bolt", printed)
            need_section = printed.split("Cards You Need to Buy", 1)[-1]
            self.assertNotIn("Ardenn, Intrepid Archaeologist", need_section)

    def test_run_convert_deck_reports_have_and_need(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_deck = tmp_path / "old.txt"
            new_deck = tmp_path / "new.txt"
            stock_csv = tmp_path / "stock.csv"

            old_deck.write_text("1 Sol Ring\n1 Island\n", encoding="utf-8")
            new_deck.write_text("1 Sol Ring\n1 Lightning Bolt\n", encoding="utf-8")

            with open(stock_csv, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Name", "Binder Name", "Quantity"])
                writer.writerow(["Sol Ring", "Binder", "1"])

            args = type(
                "Args",
                (),
                {
                    "convert_deck": [str(old_deck), str(new_deck)],
                    "convert_stock_csv": str(stock_csv),
                    "convert_binders": ["Binder"],
                    "convert_sort": "price",
                    "fresh_cache": False,
                },
            )()

            def fake_bulk_lookup(name):
                prices = {"usd": "1.00"}
                if name == "Sol Ring":
                    return {"name": "Sol Ring", "prices": prices, "type_line": "Artifact"}
                if name == "Lightning Bolt":
                    return {"name": "Lightning Bolt", "prices": prices, "type_line": "Instant"}
                if name == "Island":
                    return {"name": "Island", "prices": prices, "type_line": "Basic Land — Island"}
                return None

            with patch("edhparse.services.convert.scryfall_bulk.ensure_loaded"):
                with patch(
                    "edhparse.services.convert.scryfall_bulk.get_card_by_name_or_face",
                    side_effect=fake_bulk_lookup,
                ):
                    with patch("builtins.print") as mock_print:
                        run_convert_deck(args)

            printed = "\n".join(str(c.args[0]) for c in mock_print.call_args_list if c.args)
            self.assertIn("Cards You Have (from binders)", printed)
            self.assertIn("Current deck:", printed)
            self.assertIn("Cards You Need to Buy", printed)
            self.assertIn("Lightning Bolt", printed)
            need_section = printed.split("Cards You Need to Buy", 1)[-1]
            self.assertNotIn("Sol Ring", need_section)


if __name__ == "__main__":
    unittest.main()
