from pathlib import Path
import requests_cache
import progressbar
import requests
import json
import re
import time
from deck import Deck
from deck import Cost

# pylint: disable=missing-function-docstring, missing-class-docstring

class Collection:
    def __init__(self):
        self.decks = []
        # Be sure to cache our responses to make sure that we arent making extra calls for no reason. Expire after 24 hours for safety.
        self.session = requests_cache.CachedSession('card_cache', expire_after=3600*24)
        #self.session.cache.clear()

    def add_all_costs(self, edhr_name: str):
        self.decks.append(Deck(self.session).init_thin_deck(edhr_name, Cost.Normal))
        self.decks.append(Deck(self.session).init_thin_deck(edhr_name, Cost.Budget))
        self.decks.append(Deck(self.session).init_thin_deck(edhr_name, Cost.Expensive))

    def lookup_total_card_count(self):
        card_count = 0
        for deck in self.decks:
            if deck.commander is not None:
                card_count += 1
            for _ in deck.mainboard:
                card_count += 1
        return card_count

    def lookup_deck_data(self):
        card_count = 0
        print("\nGathering all deck information")
        print(f"total count: {self.lookup_total_card_count()}")
        with progressbar.ProgressBar(maxval=self.lookup_total_card_count()) as bar:
            bar.update(0)
            for deck in self.decks:
                card_count = deck.lookup_card_data(bar, card_count)
    
    def print_collection(self):
        sorted_decks = sorted(self.decks, key=lambda deck: deck.commander.name)
        commanders_seen = set()
        for deck in sorted_decks:
            if deck.commander.name not in commanders_seen:
                commanders_seen.add(deck.commander.name)
                print(str(deck.commander))
            print(deck)
    
    def mark_cards_owned(self, manabox_data):
        names = set()
        for row in manabox_data:
            names.add(row["Name"])
        for deck in self.decks:
            if deck.commander.name in names:
                deck.commander.owned = True
            for card in deck.mainboard:
                if card.name in names:
                    card.owned = True
