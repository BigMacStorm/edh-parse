from pathlib import Path
import requests_cache
import requests
import json
import re
import time
from deck import Deck
from deck import Cost

class Collection:
    def __init__(self):
        self.decks = []
        # Be sure to cache our responses to make sure that we arent making extra calls for no reason. Expire after 24 hours for safety.
        self.session = requests_cache.CachedSession('card_cache', expire_after=3600*24)

    def add_all_costs(self, edhr_name: str):
        self.decks.append(Deck(self.session).init_thin_deck(edhr_name, Cost.Normal))
        self.decks.append(Deck(self.session).init_thin_deck(edhr_name, Cost.Budget))
        self.decks.append(Deck(self.session).init_thin_deck(edhr_name, Cost.Expensive))

    def lookup_deck_data(self):
        for deck in self.decks:
            deck.lookup_card_data()
