from typing import Dict, Tuple
from collections import Counter

DFC_SEPARATOR = " // "


def dfc_name_aliases(name: str) -> set:
    """Return a card name plus any single-face aliases for double-faced cards."""
    name = (name or "").strip()
    if not name:
        return set()
    aliases = {name}
    if DFC_SEPARATOR in name:
        front, back = name.split(DFC_SEPARATOR, 1)
        front = front.strip()
        back = back.strip()
        if front:
            aliases.add(front)
        if back:
            aliases.add(back)
    return aliases


def register_canonical_name(name: str, canonical: str, mapping: dict) -> None:
    """Map a card name and all DFC face aliases to one canonical name."""
    canonical = (canonical or name or "").strip()
    for alias in dfc_name_aliases(name) | dfc_name_aliases(canonical):
        mapping[alias] = canonical


def is_name_resolved(name: str, mapping: dict) -> bool:
    return any(alias in mapping for alias in dfc_name_aliases(name))


def lookup_canonical(name: str, mapping: dict) -> str:
    for alias in dfc_name_aliases(name):
        if alias in mapping:
            return mapping[alias]
    return name


def deck_to_canonical(deck: Dict[str, int], mapping: dict) -> Dict[str, int]:
    counter = Counter()
    for name, qty in deck.items():
        counter[lookup_canonical(name, mapping)] += qty
    return dict(counter)


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
