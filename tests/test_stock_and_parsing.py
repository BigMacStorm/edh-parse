import csv
import tempfile
import unittest
from pathlib import Path

from core.stock import StockCollection, load_owned_quantities_from_stock_csv
from edhparse.parsing import parse_card_names_file, parse_card_names_lines


class TestStockCollection(unittest.TestCase):
    def _write_csv(self, rows):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8")
        writer = csv.writer(f)
        writer.writerow(["Name", "Binder Name", "Quantity"])
        for row in rows:
            writer.writerow(row)
        f.close()
        return f.name

    def test_quantities_and_binders(self):
        path = self._write_csv(
            [
                ["Sol Ring", "Binder", "2"],
                ["Lightning Bolt", "Stock", "1"],
                ["Counterspell", "Other", "4"],
            ]
        )
        try:
            stock = StockCollection.load(path, ["Stock", "Binder"])
            self.assertIsNone(stock.error)
            self.assertEqual(stock.quantities(), {"Sol Ring": 2, "Lightning Bolt": 1})
            order, by_binder = stock.quantities_by_binder()
            self.assertEqual(order, ["Stock", "Binder"])
            self.assertEqual(dict(by_binder["Binder"]), {"Sol Ring": 2})
            names = StockCollection.list_binder_names(path)
            self.assertEqual(names, ["Binder", "Stock", "Other"])
            compat = load_owned_quantities_from_stock_csv(path, ["Binder", "stock"])
            self.assertEqual(compat, {"Sol Ring": 2, "Lightning Bolt": 1})
        finally:
            Path(path).unlink(missing_ok=True)


class TestParseCardNamesFile(unittest.TestCase):
    def test_mtgo_style_counts(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("1 Sol Ring\n")
            f.write("Lightning Bolt\n")
            f.write("2 Counterspell\n")
            path = f.name
        try:
            names = parse_card_names_file(path)
            self.assertEqual(names, ["Sol Ring", "Lightning Bolt", "Counterspell"])
        finally:
            Path(path).unlink(missing_ok=True)

    def test_parse_lines(self):
        self.assertEqual(
            parse_card_names_lines(["1 Sol Ring", "Bolt"]),
            ["Sol Ring", "Bolt"],
        )


if __name__ == "__main__":
    unittest.main()
