from pathlib import Path
import requests_cache
import progressbar
import requests
import json
import re
import time
from deck import Deck
from deck import Cost
from itertools import chain

# pylint: disable=missing-function-docstring, missing-class-docstring

class Collection:
    def __init__(self, args):
        self.decks = []
        self.args = args
        # Be sure to cache our responses to make sure that we arent making extra calls for no reason. Expire after 24 hours for safety.
        self.session = requests_cache.CachedSession('card_cache', expire_after=3600*24*7)
        #self.session.cache.clear()

    def add_all_costs(self, edhr_name: str):
        self.decks.append(self.new_deck().init_thin_edhr_deck(edhr_name, Cost.Normal))
        self.decks.append(self.new_deck().init_thin_edhr_deck(edhr_name, Cost.Budget))
        self.decks.append(self.new_deck().init_thin_edhr_deck(edhr_name, Cost.Expensive))
    
    def add_list_deck(self, list_cards):
        card_count = len(list_cards)
        print("\nGathering list information")
        with progressbar.ProgressBar(maxval=card_count) as bar:
            bar.update(0)
            self.decks.append(self.new_deck().generate_deck_from_list(list_cards, bar))

    def lookup_total_card_count(self):
        card_count = 0
        for deck in self.decks:
            card_count += deck.get_thin_count()
        return card_count

    def lookup_deck_data(self):
        card_count = 0
        not_thin_cards = self.lookup_total_card_count()
        if not_thin_cards == 0:
            return
        print("\nGathering all deck information")
        with progressbar.ProgressBar(maxval=not_thin_cards) as bar:
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
            for card in chain(deck.mainboard, deck.sideboard):
                if card.name in names:
                    card.owned = True
                    deck.owned_set.add(card.name)
    
    def new_deck(self):
        return Deck(self.session, self.args)
