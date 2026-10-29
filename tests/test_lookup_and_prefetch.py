import json
import sqlite3
import unittest
from unittest.mock import patch

from clients import scryfall_bulk
from clients.edhrec_prefetch import edhrec_urls_for_commander, prefetch_edhrec
from core.card import Card
from core.config import RunConfig
from core.deck import Cost, Deck


def _card(cid, name):
    return {"id": cid, "name": name, "type_line": "Artifact", "layout": "normal", "released_at": "2020-01-01"}


class NameLookupTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE cards (id TEXT PRIMARY KEY, name TEXT, data TEXT)")
        self.conn.execute("CREATE INDEX idx_name ON cards(name)")
        for card in (
            _card("1", "Sol Ring"),
            _card("2", "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki"),
            _card("3", "Fire // Ice"),
        ):
            self.conn.execute("INSERT INTO cards VALUES (?, ?, ?)", (card["id"], card["name"], json.dumps(card)))
        self.conn.commit()
        self._patch = patch.object(scryfall_bulk, "_conn", self.conn)
        self._patch.start()
        scryfall_bulk._name_index = None

    def tearDown(self):
        self._patch.stop()
        scryfall_bulk._name_index = None
        self.conn.close()

    def test_exact_case_insensitive_and_front_face(self):
        get = scryfall_bulk.get_card_by_name_or_face
        self.assertEqual(get("Sol Ring")["id"], "1")
        self.assertEqual(get("sol ring")["id"], "1")
        self.assertEqual(get("Fable of the Mirror-Breaker")["id"], "2")
        self.assertEqual(get("fable of the mirror-breaker")["id"], "2")
        self.assertEqual(get("Fire // Ice")["id"], "3")
        self.assertIsNone(get("Not A Card"))

    def test_index_rebuilds_when_connection_changes(self):
        scryfall_bulk.get_card_by_name_or_face("sol ring")
        other = sqlite3.connect(":memory:")
        other.execute("CREATE TABLE cards (id TEXT PRIMARY KEY, name TEXT, data TEXT)")
        other.execute("INSERT INTO cards VALUES ('9', 'Mana Crypt', ?)", (json.dumps(_card("9", "Mana Crypt")),))
        with patch.object(scryfall_bulk, "_conn", other):
            self.assertEqual(scryfall_bulk.get_card_by_name_or_face("mana crypt")["id"], "9")
            self.assertIsNone(scryfall_bulk.get_card_by_name_or_face("sol ring"))
        other.close()


class PrefetchTests(unittest.TestCase):
    def test_urls_cover_every_variant(self):
        urls = edhrec_urls_for_commander("foo")
        self.assertEqual(len(urls), 7)
        self.assertTrue(any(u.endswith("/average-decks/foo/budget.json") for u in urls))
        self.assertTrue(any(u.endswith("/combos/foo.json") for u in urls))

    def test_skips_cached_pages(self):
        class Resp:
            status_code = 200
            from_cache = True

            def json(self):
                return {}

        class Session:
            def get(self, url, **kwargs):
                if not kwargs.get("only_if_cached"):
                    raise AssertionError("cached pages must not be fetched")
                return Resp()

        stats = prefetch_edhrec(Session(), ["foo"], workers=2)
        self.assertEqual(stats["to_fetch"], 0)


class FallbackAndPriceTests(unittest.TestCase):
    def test_budget_fallback_fetches_its_own_deck(self):
        def avg(tag):
            return [{"c": "c", "u": "cmd", "n": "Foo"}] + [
                {"c": "m", "q": 1, "u": f"{tag}{i}", "n": f"{tag}{i}"} for i in range(99)
            ]

        class FakeDeck(Deck):
            def lookup_normal(self, name):
                return {"header": "Foo", "panels": {}}

            def lookup_budget(self, name):
                return None

            def _fetch_average_deck_archidekt(self, name, edhr_data=None, cost=None):
                return avg("N" if cost == Cost.Normal else "B")

        cache = {}
        normal = FakeDeck(None, config=RunConfig(), edhr_cache=cache).init_thin_edhr_deck("foo", Cost.Normal, None)
        budget = FakeDeck(None, config=RunConfig(), edhr_cache=cache).init_thin_edhr_deck("foo", Cost.Budget, None)
        self.assertEqual(normal.mainboard[0].card_name, "N0")
        self.assertEqual(budget.mainboard[0].card_name, "B0")

    def test_partner_pair_header_resolves_to_first_commander(self):
        ids = {"Ardenn": "id-a", "Rograkh": "id-r"}

        class PairDeck(Deck):
            def _scryfall_id_by_exact_name(self, card_name):
                return ids.get(card_name)

            def lookup_normal(self, name):
                return {"header": "Ardenn // Rograkh (Commander)", "panels": {}}

            def lookup_budget(self, name):
                return {"header": "Ardenn // Rograkh (Commander) - Budget", "panels": {}}

            def edhrec_get(self):  # pragma: no cover
                return None

        class Client:
            def get_json(self, url, timeout=10):
                cards = [{"id": f"c{i}", "name": f"Card {i}", "sanitized": f"card-{i}"} for i in range(99)]
                return {"container": {"json_dict": {"cardlists": [{"cardviews": cards}]}}}, None

        cache = {}
        for cost in (Cost.Normal, Cost.Budget):
            deck = PairDeck(None, config=RunConfig(), edhr_cache=cache)
            deck.edhrec_client = Client()
            built = deck.init_thin_edhr_deck("ardenn-rograkh", cost, None)
            self.assertEqual(built.commander.card_name, "Ardenn")
            self.assertIn("Rograkh", [c.card_name for c in built.mainboard])

    def test_unpriced_flag(self):
        card = Card(None)
        card.parse_scryfall_card({"name": "X", "type_line": "Artifact", "prices": {"usd": None, "usd_foil": None}})
        self.assertTrue(card.unpriced)
        self.assertEqual(card.price, 0.0)
        card = Card(None)
        card.parse_scryfall_card({"name": "Forest", "type_line": "Basic Land — Forest", "prices": {"usd": None}})
        self.assertFalse(card.unpriced)
        card = Card(None)
        card.parse_scryfall_card({"name": "Y", "type_line": "Artifact", "prices": {"usd": "2.50"}})
        self.assertFalse(card.unpriced)


if __name__ == "__main__":
    unittest.main()
