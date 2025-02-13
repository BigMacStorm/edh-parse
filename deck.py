from card import Card
import progressbar
import time
from enum import Enum
import requests
import json
import re
import time
import random
from itertools import chain

# pylint: disable=missing-function-docstring, missing-class-docstring



user_agent_list = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.82 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_4_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Mobile/15E148 Safari/604.1",
    "Mozilla/4.0 (compatible; MSIE 9.0; Windows NT 6.1)",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.141 Safari/537.36 Edg/87.0.664.75",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/70.0.3538.102 Safari/537.36 Edge/18.18363",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:53.0) Gecko/20100101 Firefox/53.0",
    "Mozilla/5.0 (compatible; MSIE 9.0; Windows NT 6.0; Trident/5.0; Trident/5.0)",
    "Mozilla/5.0 (compatible; MSIE 10.0; Windows NT 6.2; Trident/6.0; MDDCJS)",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/51.0.2704.79 Safari/537.36 Edge/14.14393",
    "Mozilla/4.0 (compatible; MSIE 6.0; Windows NT 5.1; SV1)",
]

class Cost(Enum):
    Normal = 1
    Budget = 2
    Expensive = 3

class Chart:
    def __init__(self, edhr_data):
        self.creature = edhr_data["creature"] if "creature" in edhr_data else None
        self.instant = edhr_data["instant"] if "instant" in edhr_data else None
        self.sorcery = edhr_data["sorcery"] if "sorcery" in edhr_data else None
        self.artifact = edhr_data["artifact"] if "artifact" in edhr_data else None
        self.enchantment = edhr_data["enchantment"] if "enchantment" in edhr_data else None
        self.battle = edhr_data["battle"] if "battle" in edhr_data else None
        self.planeswalker = edhr_data["planeswalker"] if "planeswalker" in edhr_data else None
        self.land = edhr_data["land"] if "land" in edhr_data else None
        self.basic = edhr_data["basic"] if "basic" in edhr_data else None
        self.nonbasic = edhr_data["nonbasic"] if "nonbasic" in edhr_data else None

class Deck:
    def __init__(self, session, args):
        self.mainboard = []
        self.sideboard = []
        self.commander = None
        self.chart = None
        self.title = None
        self.session = session
        self.error = False
        self.args = args
        self.owned_set = set()

    def __str__(self):
        deck_info = f"Deck information for: {self.title}\n"
        deck_info += f"Total Cost: {round(self.get_cost(), 2)}\n"
        deck_info += f"Total Cards: {self.get_card_count()}\n"
        if self.error:
            deck_info = f"Deck is malformed, card count: {len(self.mainboard)}\n"
        owned_count = self.get_owned_count()
        if owned_count > 0:
            deck_info += f"Owned Count: {owned_count}\n"
            deck_info += f"Not owned cost: {round(self.get_cost(only_not_owned=True), 2)}\n"
        error_count = self.get_error_count()
        if error_count > 0:
            deck_info += f"Error count: {self.get_error_count()}\n"
        if self.args.show_owned:
            deck_info += f"\nOwned cards:\n"
            deck_info += "\n".join(str(item) for item in self.owned_set)
            deck_info += "\n\n"
        return deck_info
    
    def get_card_count(self):
        count = 0
        if self.commander is not None:
            count += 1
        count += len(self.mainboard)
        count += len(self.sideboard)
        return count
    
    def get_cost(self, only_not_owned=False):
        cost = 0.0
        if only_not_owned:
            if not self.commander.owned:
                cost += self.commander.price
        else:
            cost += self.commander.price
        for card in chain(self.mainboard, self.sideboard):
            if only_not_owned:
                if not card.owned:
                    cost += card.price
            else:
                cost += card.price
        return cost
    
    def get_thin_count(self):
        card_count = 0
        if self.commander is not None and self.commander.thin:
            card_count += 1
        for card in self.mainboard:
            if card.thin:
                card_count += 1
        for card in self.sideboard:
            if card.thin:
                card_count += 1
        return card_count

    
    def get_owned_count(self):
        owned_count = 0
        if self.commander.owned:
            owned_count += 1
        for card in chain(self.mainboard, self.sideboard):
            if card.owned:
                owned_count += 1
        return owned_count
    
    def get_error_count(self):
        error_count = 0
        for card in chain(self.mainboard, self.sideboard):
            if card.error:
                error_count += 1
        return error_count

    def lookup_card_data(self, bar, card_count):
        if self.commander.thin:
            self.commander.get_data()
            card_count += 1
        bar.update(card_count)
        if self.get_thin_count() == 0:
            return
        print(f"\nGetting mainboard for {self.title}")
        for card in chain(self.mainboard, self.sideboard):
            card.get_data()
            card_count += 1
            bar.update(card_count)
        return card_count

    def init_thin_edhr_deck(self, edhr_name: str, cost: Cost):
        edhr_data = None
        if cost == Cost.Normal:
            edhr_data = self.lookup_normal(edhr_name)
        elif cost == Cost.Budget:
            edhr_data = self.lookup_budget(edhr_name)
        elif cost == Cost.Expensive:
            edhr_data = self.lookup_expensive(edhr_name)
        self.build_id_deck_from_edhr(edhr_data)
        return self
    
    def generate_deck_from_list(self, list_cards, bar):
        count = 0
        for name, data in list_cards.items():
            self.add_card_from_list(name, data)
            count += 1
            bar.update(count)
        return self
    
    def add_card_from_list(self, name, data):
        is_commander = data["header"] == "commander"
        is_mainboard = data["header"] == "mainboard"
        is_sideboard = data["header"] == "sideboard"
        quantity = data["quantity"]
        card = Card(self.session, self.args, is_commander=is_commander)
        card.search_card(name)
        if is_commander:
            self.commander = card
            self.title = card.name
            return
        elif is_mainboard:
            for _ in range(quantity):
                self.mainboard.append(card)
            return
        elif is_sideboard:
            for _ in range(quantity):
                self.sideboard.append(card)
            return
        else:
            print(f"Did not find a valid card in this line: {name} - {data}")
            return

    
    def build_id_deck_from_edhr(self, edhr_data):
        self.title = edhr_data["header"] if "header" in edhr_data else None
        for card in edhr_data["archidekt"]:
            if card["c"] == "c":
                self.commander = Card(self.session, self.args, card_id=card["u"], is_commander=True, is_thin=True)
            elif card["c"] == "m":
                for _ in range(card["q"]):
                    self.mainboard.append(Card(self.session, self.args, card_id=card["u"], is_thin=True))
            else:
                print(f"found something weird: {card}")
        if self.commander is None or len(self.mainboard) != 99:
            self.error = True

    def lookup_commander(self, url: str):
        try:
            response = self.session.get(url, timeout=2)
            response.raise_for_status()
            if not response.from_cache:
                time.sleep(0.1)
            edhr_data = response.json()
            return edhr_data

        except requests.exceptions.RequestException as err:
            print(f"Error looking up edhrec commander: {err}")
    
    def lookup_normal(self, commander_name: str):
        url = f"https://json.edhrec.com/pages/commanders/{commander_name}.json"
        return self.lookup_commander(url)
    
    def lookup_budget(self, commander_name: str):
        url = f"https://json.edhrec.com/pages/commanders/{commander_name}/budget.json"
        return self.lookup_commander(url)
    
    def lookup_expensive(self, commander_name: str):
        url = f"https://json.edhrec.com/pages/commanders/{commander_name}/expensive.json"
        return self.lookup_commander(url)