from typing import Dict, Tuple
from collections import Counter

def diff_decks(old_deck: Dict[str, int], new_deck: Dict[str, int]) -> Tuple[Dict[str, int], Dict[str, int]]:
    """
    Compares two decklists and returns the added and removed cards.
    """
    old_counter = Counter(old_deck)
    new_counter = Counter(new_deck)

    added = new_counter - old_counter
    removed = old_counter - new_counter

    return dict(added), dict(removed)

def generate_shopping_list(needed_cards: Dict[str, int], owned_cards: Dict[str, int]) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, int]]:
    """
    Filters a list of needed cards against a collection to produce a shopping list.
    """
    needed_counter = Counter(needed_cards)
    owned_counter = Counter(owned_cards)

    # Cards to buy is what's needed minus what's owned
    cards_to_buy = needed_counter - owned_counter

    # Owned from needed is the intersection of the two counters
    cards_owned_from_needed = needed_counter & owned_counter

    return dict(needed_cards), dict(cards_owned_from_needed), dict(cards_to_buy)
