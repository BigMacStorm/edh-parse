import unittest
from unittest.mock import patch

from edhparse.pivot import (
    CommanderHit,
    PricedCard,
    aggregate_commander_scores,
    build_pivot_report,
    edhrec_card_slug,
    extract_top_commanders,
    select_expensive_cards,
)


class TestPivotHelpers(unittest.TestCase):
    def test_edhrec_card_slug(self):
        self.assertEqual(edhrec_card_slug("Rhystic Study"), "rhystic-study")
        self.assertEqual(edhrec_card_slug("Y'shtola, Night's Blessed"), "yshtola-nights-blessed")
        self.assertEqual(
            edhrec_card_slug("Ojer Axonil, Deepest Might // Temple of Power"),
            "ojer-axonil-deepest-might",
        )

    def test_extract_top_commanders(self):
        page = {
            "container": {
                "json_dict": {
                    "cardlists": [
                        {
                            "header": "New Commanders",
                            "cardviews": [
                                {"name": "Ignore Me", "num_decks": 10, "potential_decks": 10}
                            ],
                        },
                        {
                            "header": "Top Commanders",
                            "cardviews": [
                                {
                                    "name": "Kinnan, Bonder Prodigy",
                                    "slug": "kinnan-bonder-prodigy",
                                    "num_decks": 50,
                                    "potential_decks": 100,
                                },
                                {
                                    "name": "No Stats",
                                    "slug": "no-stats",
                                },
                            ],
                        },
                    ]
                }
            }
        }
        hits = extract_top_commanders(page)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].name, "Kinnan, Bonder Prodigy")
        self.assertEqual(hits[0].slug, "kinnan-bonder-prodigy")
        self.assertAlmostEqual(hits[0].inclusion_pct, 50.0)

    def test_select_expensive_cards_skips_lands_and_cheap(self):
        cards = [
            PricedCard("Sol Ring", 2.0, type_line="Artifact"),
            PricedCard("Rhystic Study", 40.0, type_line="Enchantment"),
            PricedCard("Ancient Tomb", 80.0, type_line="Land"),
            PricedCard("Dryad Arbor", 12.0, type_line="Land Creature — Forest Dryad"),
        ]
        selected = select_expensive_cards(cards, min_price=5.0, skip_lands=True)
        names = [c.name for c in selected]
        self.assertEqual(names, ["Rhystic Study", "Dryad Arbor"])

    def test_aggregate_weighted_by_price_and_inclusion(self):
        card_a = PricedCard("Rhystic Study", 40.0)
        card_b = PricedCard("Cyclonic Rift", 30.0)
        card_commanders = [
            (
                card_a,
                [
                    CommanderHit("Atraxa", "atraxa", 50.0),
                    CommanderHit("Current", "current-com", 90.0),
                ],
            ),
            (
                card_b,
                [
                    CommanderHit("Atraxa", "atraxa", 20.0),
                    CommanderHit("Other", "other", 10.0),
                ],
            ),
        ]
        aggs = aggregate_commander_scores(
            card_commanders,
            exclude_commander_names=["Current"],
            min_inclusion_pct=0.0,
        )
        self.assertEqual([a.slug for a in aggs], ["atraxa", "other"])
        atraxa = aggs[0]
        # 40*0.5 + 30*0.2 = 20 + 6 = 26
        self.assertAlmostEqual(atraxa.weighted_score, 26.0)
        self.assertAlmostEqual(atraxa.retained_value, 70.0)
        self.assertEqual(len(atraxa.cards), 2)

    def test_build_pivot_report_retained_pct(self):
        card_a = PricedCard("Rhystic Study", 40.0)
        card_b = PricedCard("Cyclonic Rift", 30.0)
        aggs = aggregate_commander_scores(
            [
                (card_a, [CommanderHit("Atraxa", "atraxa", 50.0)]),
                (card_b, [CommanderHit("Atraxa", "atraxa", 20.0)]),
            ]
        )
        with patch("edhparse.pivot.enrich_commanders_with_scryfall"):
            report = build_pivot_report(
                source_label="inputs/demo",
                expensive_cards=[card_a, card_b],
                aggregates=aggs,
                excluded_commander="Demo",
                min_price=5.0,
                top_n=10,
            )
        self.assertEqual(report["expensive_card_count"], 2)
        self.assertAlmostEqual(report["total_expensive_value"], 70.0)
        self.assertEqual(report["commander_count"], 1)
        self.assertAlmostEqual(report["commanders"][0]["retained_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()
