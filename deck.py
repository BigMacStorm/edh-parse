from card import Card
import progressbar
import time
from enum import Enum
import requests
import json
import re
import time

# pylint: disable=missing-function-docstring, missing-class-docstring

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
    def __init__(self, session):
        self.mainboard = []
        self.commander = None
        self.chart = None
        self.title = None
        self.session = session
        self.error = False

    def __str__(self):
        deck_info = f"Deck information for: {self.title}\n"
        deck_info += f"Total Cost: {round(self.get_cost(), 2)}\n"
        if self.error:
            deck_info = f"Deck is malformed, card count: {len(self.mainboard)}\n"
        owned_count = self.get_owned_count()
        if owned_count > 0:
            deck_info += f"Owned Count: {owned_count}\n"
            deck_info += f"Not owned cost: {round(self.get_cost(only_not_owned=True), 2)}\n"
        error_count = self.get_error_count()
        if error_count > 0:
            deck_info += f"Error count: {self.get_error_count()}\n"
        return deck_info
    
    def get_cost(self, only_not_owned=False):
        cost = 0.0
        cost += self.commander.price
        for card in self.mainboard:
            if only_not_owned:
                if not card.owned:
                    cost += card.price
            else:
                cost += card.price
        return cost
    
    def get_owned_count(self):
        owned_count = 0
        for card in self.mainboard:
            if card.owned:
                owned_count += 1
        return owned_count
    
    def get_error_count(self):
        error_count = 0
        for card in self.mainboard:
            if card.error:
                error_count += 1
        return error_count

    def lookup_card_data(self, bar, card_count):
        self.commander.get_data()
        card_count += 1
        bar.update(card_count)
        print(f"\nGetting mainboard for {self.title}")
        for card in self.mainboard:
            card.get_data()
            card_count += 1
            bar.update(card_count)
        return card_count

    def init_thin_deck(self, edhr_name: str, cost: Cost):
        edhr_data = None
        if cost == Cost.Normal:
            edhr_data = self.lookup_normal(edhr_name)
        elif cost == Cost.Budget:
            edhr_data = self.lookup_budget(edhr_name)
        elif cost == Cost.Expensive:
            edhr_data = self.lookup_expensive(edhr_name)
        self.build_id_deck_from_edhr(edhr_data)
        return self
    
    def build_id_deck_from_edhr(self, edhr_data):
        self.title = edhr_data["header"] if "header" in edhr_data else None
        for card in edhr_data["archidekt"]:
            if card["c"] == "c":
                self.commander = Card(card["u"], self.session, True)
            elif card["c"] == "m":
                for _ in range(card["q"]):
                    self.mainboard.append(Card(card["u"], self.session))
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
