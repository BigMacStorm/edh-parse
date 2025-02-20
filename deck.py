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
from typing import Dict, Any

# pylint: disable=missing-function-docstring, missing-class-docstring

class Cost(Enum):
    Normal = 1
    Budget = 2
    Expensive = 3

class Chart:
    def __init__(self, piechart_data: Dict[str, Any]):
        if piechart_data:
            for section in piechart_data:
                setattr(self, section["label"], int(section["value"]))
    
    def __str__(self):
        return f"Piechart: {self.__dict__}"
    
    def output_string(self):
        out_string = ""
        for key, value in self.__dict__.items():
            out_string += f"{key}: {value}\n"
        return out_string

class Deck:
    def __init__(self, session, args):
        self.mainboard = []
        self.sideboard = []
        self.commander = None
        self.commander_subtype = None
        self.chart = None
        self.title = None
        self.session = session
        self.error = False
        self.args = args
        self.cost = None
        self.owned_set = set()
        self.deck_type = None
        self.popular_tag = None

    def __str__(self):
        deck_info = f"Deck information for: {self.title}\n"
        deck_info += f"Total Cost: {round(self.get_cost(), 2)}\n"
        deck_info += f"Total Cards: {self.get_card_count()}\n"
        if self.popular_tag:
            deck_info += f"Most popular tag: {self.popular_tag}\n"
        if self.deck_type:
            deck_info += f"Deck type: {self.deck_type}\n"
        if self.error:
            deck_info += f"Deck is malformed, card count: {len(self.mainboard)}\n"
        owned_count = self.get_owned_count()
        if owned_count > 0:
            deck_info += f"Owned Count: {owned_count}\n"
            deck_info += f"Not owned cost: {round(self.get_cost(only_not_owned=True), 2)}\n"
        error_count = self.get_error_count()
        if error_count > 0:
            deck_info += f"Error count: {self.get_error_count()}\n"
        if self.chart:
            deck_info += str(self.chart)
            deck_info += "\n"
        if self.args.show_owned:
            sorted_list = list(self.owned_set)
            sorted_list.sort(reverse=True)
            deck_info += "\nOwned cards:\n"
            deck_info += "\n".join(f"${price:.2f} - {name}" for price, name in sorted_list)
            deck_info += "\n\n"
        if self.args.all:
            sorted_list = sorted(list(self.mainboard), key=lambda card: card.price, reverse=True)
            deck_info += "\nMainboard Cards:\n"
            deck_info += "\n".join(f"${card.price:.2f} - {card.name}" for card in sorted_list)
            if len(self.sideboard) > 0:
                sorted_list = sorted(list(self.sideboard), key=lambda card: card.price, reverse=True)
                deck_info += "\nSideboard Cards:"
                deck_info += "\n".join(f"${card.price:.2f} - {card.name}" for card in sorted_list)
            deck_info += "\n"
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
        if self.get_thin_count() == 0:
            return
        for card in chain(self.mainboard, self.sideboard):
            card.get_data()
            card_count += 1
            bar.update(card_count, info=f"Getting {self.title}")
        return card_count

    def init_thin_edhr_deck(self, edhr_name: str, cost: Cost, deck_type: str):
        edhr_data = None
        self.deck_type = deck_type
        self.cost = cost
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
            bar.update(count, info="Gathering list information")
        return self
    
    def build_dict(self):
        output = {}
        output["card_pic"] = self.commander.card_pic
        output["name"] = self.commander.name
        output["budget"] = self.cost
        output["color_identity"] = self.commander.color_identity
        output["owned_percentage"] = f"{100 * (self.get_owned_count() / self.get_card_count()):.2f}"
        output["total_cost"] = f"{self.get_cost():.2f}"
        output["not_owned_cost"] = f"{self.get_cost(only_not_owned=True):.2f}"
        output["type_line"] = self.commander.type_line.replace("Legendary Creature — ", "")
        output["oracle_text"] = self.commander.oracle_text
        output["color_identity"] = self.commander.color_identity
        output["artist"] = self.commander.artist
        output["deck_type"] = self.deck_type
        output["popular_tag"] = self.popular_tag
        output["cmc"] = self.commander.cmc
        output["game_changer_count"] = self.get_game_changer_count()
        output["Lands"] = getattr(self.chart, "Land", 0)
        output["Enchantments"] = getattr(self.chart, "Enchantment", 0)
        output["Planeswalkers"] = getattr(self.chart, "Planeswalker", 0)
        output["Artifacts"] = getattr(self.chart, "Artifact", 0)
        output["Sorceries"] = getattr(self.chart, "Sorcery", 0)
        output["Instants"] = getattr(self.chart, "Instant", 0)
        output["Creatures"] = getattr(self.chart, "Creature", 0)
        output["Battles"] = getattr(self.chart, "Battle", 0)
        return output
    
    def get_game_changer_count(self):
        gc_count = 0
        if self.commander.game_changer:
            gc_count += 1
        for card in chain(self.mainboard, self.sideboard):
            if card.game_changer:
                gc_count += 1
        return gc_count

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
        if edhr_data.get("panels", {}).get("piechart", {}).get("content"):
            self.chart = Chart(edhr_data.get("panels", {}).get("piechart", {}).get("content"))
        if edhr_data.get("panels", {}).get("taglinks"):
            self.popular_tag = edhr_data.get("panels", {}).get("taglinks")[0].get("slug")
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