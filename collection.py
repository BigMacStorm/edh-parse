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
import csv

# pylint: disable=missing-function-docstring, missing-class-docstring

class Collection:
    def __init__(self, args):
        self.decks = []
        self.args = args
        # Be sure to cache our responses to make sure that we arent making extra calls for no reason. Expire after 31 days for safety.
        self.session = requests_cache.CachedSession('card_cache', expire_after=3600*24*31)
        if self.args.fresh_cache:
            self.session.cache.clear()

    def add_all_costs(self, just_name: str=None, name_with_type=None):
        edhr_name = None
        deck_type = None
        if just_name:
            edhr_name = just_name
        elif name_with_type:
            if name_with_type[1]:
                edhr_name = f"{str(name_with_type[0])}/{str(name_with_type[1])}"
                deck_type = name_with_type[1]
            else:
                edhr_name = name_with_type[0]
        else:
            return
        self.decks.append(self.new_deck().init_thin_edhr_deck(edhr_name, Cost.Normal, deck_type=deck_type))
        self.decks.append(self.new_deck().init_thin_edhr_deck(edhr_name, Cost.Budget, deck_type=deck_type))
        self.decks.append(self.new_deck().init_thin_edhr_deck(edhr_name, Cost.Expensive, deck_type=deck_type))
    
    def add_list_deck(self, list_cards):
        card_count = len(list_cards)
        with progressbar.ProgressBar(prefix="{variables.info}", variables={'info': '--'}, maxval=card_count, initial_value=0) as bar:
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
        with progressbar.ProgressBar(prefix="{variables.info}", variables={'info': '--'}, maxval=not_thin_cards, initial_value=0) as bar:
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
                    deck.owned_set.add((card.price, card.name))
    
    def new_deck(self):
        return Deck(self.session, self.args)
    
    def write_to_file(self):
        fieldnames = [
                        "card_pic",
                        "name",
                        "budget",
                        "deck_type",
                        "popular_tag",
                        "color_identity",
                        "owned_percentage",
                        "game_changer_count",
                        "total_cost",
                        "not_owned_cost",
                        "type_line",
                        "oracle_text",
                        "cmc",
                        "artist",
                        "Lands",
                        "Enchantments",
                        "Planeswalkers",
                        "Artifacts",
                        "Sorceries",
                        "Instants",
                        "Creatures",
                        "Battles"]

        output = []
        for deck in self.decks:
            output.append(deck.build_dict())
        
        filename = self.args.csv_file if self.args.csv_file else "csv_out.csv"

        try:
            with open(filename, 'w', newline='', encoding='utf-8') as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(output)
        except Exception as e:
            print(f"Error writing to CSV: {e}")
