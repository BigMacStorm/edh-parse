import unittest
from unittest.mock import patch

from edhparse.edhrec_commander_compare import (
    dice_similarity_pct,
    fetch_commander_cards_inclusions,
    mean_overlap_pct,
    parse_edhrec_commander_slug,
)


class TestEdhrecCommanderCompare(unittest.TestCase):
    def test_parse_full_url_with_theme(self):
        self.assertEqual(
            parse_edhrec_commander_slug(
                "https://edhrec.com/commanders/toph-earthbending-master/landfall"
            ),
            "toph-earthbending-master/landfall",
        )

    def test_parse_strips_price_tier(self):
        self.assertEqual(
            parse_edhrec_commander_slug(
                "https://edhrec.com/commanders/toph-earthbending-master/budget"
            ),
            "toph-earthbending-master",
        )

    def test_parse_bare_slug(self):
        self.assertEqual(parse_edhrec_commander_slug("toph-earthbending-master"), "toph-earthbending-master")

    def test_mean_overlap_equal_decks(self):
        a = {"x", "y", "z"}
        b = {"x", "y", "w"}
        # intersection 2, |A|=3, |B|=3 -> (2/3+2/3)/2 * 100, rounded to 1 decimal
        self.assertEqual(mean_overlap_pct(a, b), 66.7)

    def test_fetch_inclusion_uses_average_deck_json_only(self):
        """Budget/expensive cohort stats must not attach to cards missing from primary."""

        def _resp(cardviews):
            return type(
                "R",
                (),
                {
                    "from_cache": True,
                    "json": lambda self, _cv=cardviews: {
                        "container": {
                            "header": "X (Commander)",
                            "json_dict": {"cardlists": [{"cardviews": _cv}]},
                        }
                    },
                },
            )()

        def fake_get(_session, url, timeout=10):
            if url.endswith("/pages/commanders/korv.json"):
                return _resp(
                    [{"name": "On Primary", "num_decks": 100, "potential_decks": 1000}]
                ), None
            if "/korv/budget.json" in url:
                return _resp([]), None
            if "/korv/expensive.json" in url:
                return (
                    _resp(
                        [
                            {
                                "name": "Only Expensive",
                                "num_decks": 50,
                                "potential_decks": 100,
                            }
                        ]
                    ),
                    None,
                )
            return None, "unexpected url"

        with patch("edhparse.edhrec_commander_compare._edhrec_get_with_retry", fake_get):
            cards, title = fetch_commander_cards_inclusions(None, "korv")

        self.assertEqual(title, "X")
        self.assertAlmostEqual(cards["On Primary"][0], 10.0)
        self.assertIsNone(cards["Only Expensive"][0])
        self.assertEqual(cards["Only Expensive"][1], "—")

    def test_dice_equal_size_example(self):
        a = set(range(100))
        b = set(range(30)) | set(range(30, 100))
        # |A|=100, |B|=100, |∩|=70? Let me use 30 overlap: A = 0..99, B = 70..169 -> |∩| = 30? 
        # A = 0-99, B = 70-169, intersection = 70..99 = 30 cards
        a = set(range(100))
        b = set(range(70, 170))
        inter = len(a & b)
        self.assertEqual(inter, 30)
        self.assertAlmostEqual(mean_overlap_pct(a, b), 30.0)
        self.assertAlmostEqual(dice_similarity_pct(a, b), 30.0)


if __name__ == "__main__":
    unittest.main()
