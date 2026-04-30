import unittest
from core.deck_diff import diff_decks, generate_shopping_list

class TestDeckDiff(unittest.TestCase):

    def test_diff_decks(self):
        old_deck = {"Card A": 1, "Card B": 2, "Card C": 1}
        new_deck = {"Card A": 1, "Card B": 1, "Card D": 3}
        
        added, removed = diff_decks(old_deck, new_deck)
        
        self.assertEqual(added, {"Card D": 3})
        self.assertEqual(removed, {"Card B": 1, "Card C": 1})

    def test_generate_shopping_list(self):
        needed_cards = {"Card A": 2, "Card B": 1, "Card C": 3}
        owned_cards = {"Card A": 1, "Card B": 2, "Card D": 1}

        all_needed, owned_from_needed, to_buy = generate_shopping_list(needed_cards, owned_cards)

        self.assertEqual(all_needed, needed_cards)
        self.assertEqual(owned_from_needed, {"Card A": 1, "Card B": 1})
        self.assertEqual(to_buy, {"Card A": 1, "Card C": 3})

if __name__ == '__main__':
    unittest.main()
