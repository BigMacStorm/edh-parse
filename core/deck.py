from core.card import Card, _get_with_retry, SCRYFALL_SLEEP
from utils.http_retry import get_with_retry as _http_get_with_retry
from clients.edhrec_client import EdhrecClient
from clients.scryfall_client import ScryfallClient
from clients import scryfall_bulk
import time
from enum import Enum
import requests
import json
import re
import time
import random
from itertools import chain
from collections import Counter
from typing import Dict, Any

# pylint: disable=missing-function-docstring, missing-class-docstring

# Slightly longer delay after EDHREC requests to avoid rate limits
EDHREC_SLEEP = 0.25


def _edhrec_get_with_retry(session, url, timeout=10, max_retries=5):
    """Backwards-compatible wrapper for EDHREC lookups."""

    def _log_rate_limited(attempt: int, max_attempts: int, wait: int) -> None:
        _ = (max_attempts, attempt)
        print(f"Rate limited by EDHREC; backing off {wait}s...")

    return _http_get_with_retry(
        session,
        url,
        params=None,
        timeout=timeout,
        max_retries=max_retries,
        on_rate_limited=_log_rate_limited,
    )


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
    def __init__(self, session, args, edhr_cache=None):
        self.mainboard = []
        self.sideboard = []
        self.commander = None
        self.commander_subtype = None
        self.chart = None
        self.title = None
        self.session = session
        self.args = args
        self.edhr_cache = edhr_cache if edhr_cache is not None else {}
        self.edhrec_client = EdhrecClient(self.session, sleep_seconds=EDHREC_SLEEP)
        self.scryfall_client = ScryfallClient(self.session, sleep_seconds=SCRYFALL_SLEEP)
        self.error = False
        self.cost = None
        self.owned_set = set()
        self.deck_type = None
        self.popular_tag = None
        self.edhr_slug = None  # base commander slug for EDHREC URLs
        self.taglinks = []  # full list of tags from EDHREC (for top 3 display)

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
        if self.args.need:
            needed_cards = []
            if not self.commander.owned:
                needed_cards.append(self.commander)
            for card in chain(self.mainboard, self.sideboard):
                if not card.owned:
                    needed_cards.append(card)

            if needed_cards:
                card_counts = Counter(card.name for card in needed_cards)
                unique_cards = {card.name: card for card in reversed(needed_cards)}
                
                sorted_card_names = sorted(unique_cards.keys(), key=lambda name: unique_cards[name].price, reverse=True)
                
                deck_info += "\nCards still needed:\n"
                card_list = [f"{card_counts[name]} {name}" for name in sorted_card_names]
                deck_info += "\n".join(card_list)
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
        commander_price = float(self.commander.price or 0.0) if self.commander is not None else 0.0
        if only_not_owned:
            if not self.commander.owned:
                cost += commander_price
        else:
            cost += commander_price
        for card in chain(self.mainboard, self.sideboard):
            card_price = float(card.price or 0.0)
            if only_not_owned:
                if not card.owned:
                    cost += card_price
            else:
                cost += card_price
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
        if self.commander is None:
            return card_count
        if self.commander.thin:
            self.commander.get_data()
            card_count += 1
        if self.get_thin_count() == 0:
            return card_count
        for card in chain(self.mainboard, self.sideboard):
            card.get_data()
            card_count += 1
            bar.update(card_count, info=f"Getting {self.title}")
        return card_count

    def init_thin_edhr_deck(self, edhr_name: str, cost: Cost, deck_type: str):
        return DeckBuilder(self).init_thin_edhr_deck(edhr_name, cost, deck_type)
    
    def generate_deck_from_list(self, list_cards, bar):
        return DeckBuilder(self).generate_deck_from_list(list_cards, bar)
    
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
        DeckBuilder(self).add_card_from_list(name, data)

    
    def build_id_deck_from_edhr(self, edhr_data):
        DeckBuilder(self).build_id_deck_from_edhr(edhr_data)

    def lookup_commander(self, url: str):
        data, err = self.edhrec_client.get_json(url, timeout=10)
        if err:
            print(f"Error looking up edhrec commander: {err}")
            return None
        return data
    
    def _fetch_average_deck_archidekt(self, commander_name: str, edhr_data: dict = None, cost: Cost = None):
        """Fetch average-decks page for this variant (normal/budget/expensive) and return archidekt-style list."""
        if cost == Cost.Budget:
            url = f"https://json.edhrec.com/pages/average-decks/{commander_name}/budget.json"
        elif cost == Cost.Expensive:
            url = f"https://json.edhrec.com/pages/average-decks/{commander_name}/expensive.json"
        else:
            url = f"https://json.edhrec.com/pages/average-decks/{commander_name}.json"
        data, err = self.edhrec_client.get_json(url, timeout=10)
        if err:
            print(f"Error fetching average deck for {commander_name}: {err}")
            return None
        container = data.get("container") or {}
        json_dict = container.get("json_dict") or {}
        cardlists = json_dict.get("cardlists") or []
        archidekt = []
        commander_id = None
        commander_display_name = None
        # Reuse commander ID from Normal deck (or previous resolve) so we never call Scryfall for same commander
        commander_id = self.edhr_cache.get(commander_name + "_commander_id")
        for section in cardlists:
            for card in section.get("cardviews") or []:
                cid = card.get("id")
                sanitized = (card.get("sanitized") or "").strip()
                if not cid:
                    continue
                name = card.get("name")
                if sanitized == commander_name:
                    commander_id = cid
                    commander_display_name = name
                    self.edhr_cache[commander_name + "_commander_id"] = cid
                    archidekt.append({"c": "c", "u": cid, "n": name})
                else:
                    archidekt.append({"c": "m", "q": 1, "u": cid, "n": name})
        if not commander_id and edhr_data:
            header = self._normalize_commander_header(edhr_data.get("header") or "")
            if header:
                commander_id = self._scryfall_id_by_exact_name(header)
                if commander_id:
                    self.edhr_cache[commander_name + "_commander_id"] = commander_id
                    archidekt.insert(0, {"c": "c", "u": commander_id, "n": header})
        elif commander_id and not commander_display_name and edhr_data:
            # Had commander_id from cache but it wasn't in this variant's list; insert it
            header = self._normalize_commander_header(edhr_data.get("header") or "")
            if header:
                archidekt.insert(0, {"c": "c", "u": commander_id, "n": header})
        if not commander_id:
            return None
        return archidekt

    def _scryfall_id_by_exact_name(self, card_name: str):
        """Return Scryfall UUID for a card by exact name, or None."""
        data, err = self.scryfall_client.get_card_by_name_exact(card_name, timeout=5, max_retries=5)
        if err or data is None:
            return None
        return data.get("id")

    @staticmethod
    def _normalize_commander_header(header: str) -> str:
        """Strip EDHREC commander header decorations and price-variant suffixes."""
        text = (header or "").replace(" (Commander)", "").strip()
        text = re.sub(r"\s*-\s*(Budget|Expensive)\s*$", "", text, flags=re.IGNORECASE).strip()
        return text

    def lookup_normal(self, commander_name: str):
        url = f"https://json.edhrec.com/pages/commanders/{commander_name}.json"
        return self.lookup_commander(url)
    
    def lookup_budget(self, commander_name: str):
        url = f"https://json.edhrec.com/pages/commanders/{commander_name}/budget.json"
        return self.lookup_commander(url)
    
    def lookup_expensive(self, commander_name: str):
        url = f"https://json.edhrec.com/pages/commanders/{commander_name}/expensive.json"
        return self.lookup_commander(url)


class DeckBuilder:
    """Build/populate a `Deck` instance from EDHREC payloads or decklist inputs."""

    def __init__(self, deck: Deck):
        self.deck = deck

    def init_thin_edhr_deck(self, edhr_name: str, cost: Cost, deck_type: str) -> Deck:
        deck = self.deck
        edhr_data = None
        deck.deck_type = deck_type
        deck.cost = cost

        # Always use base commander slug only — never sub-theme paths (e.g. /artifacts)
        base_name = (edhr_name or "").split("/")[0].strip()
        if cost == Cost.Normal:
            edhr_data = deck.lookup_normal(base_name)
        else:
            if cost == Cost.Budget:
                edhr_data = deck.lookup_budget(base_name)
            else:
                edhr_data = deck.lookup_expensive(base_name)

            # Commander page for header/panels; reuse cached Normal if Budget/Expensive 404
            if edhr_data is None and base_name:
                edhr_data = deck.edhr_cache.get(base_name) or deck.lookup_normal(base_name)

        if base_name and edhr_data and not edhr_data.get("archidekt"):
            deck.edhr_cache[base_name] = edhr_data

        # EDHREC has three average decks: normal, budget, expensive. Fetch the one for this cost.
        if edhr_data and "archidekt" not in edhr_data:
            archidekt = deck._fetch_average_deck_archidekt(base_name or edhr_name, edhr_data, cost)
            if archidekt:
                edhr_data["archidekt"] = archidekt

        self.build_id_deck_from_edhr(edhr_data)
        deck.edhr_slug = base_name
        return deck

    def generate_deck_from_list(self, list_cards, bar) -> Deck:
        deck = self.deck
        count = 0
        for name, data in list_cards.items():
            self.add_card_from_list(name, data)
            count += 1
            bar.update(count, info="Gathering list information")
        return deck

    def add_card_from_list(self, name, data) -> None:
        deck = self.deck
        is_commander = data["header"] == "commander"
        is_mainboard = data["header"] == "mainboard"
        is_sideboard = data["header"] == "sideboard"
        quantity = data["quantity"]

        card = Card(deck.session, deck.args, is_commander=is_commander)
        card.search_card(name)

        if is_commander:
            deck.commander = card
            deck.title = card.name
            return
        if is_mainboard:
            for _ in range(quantity):
                deck.mainboard.append(card)
            return
        if is_sideboard:
            for _ in range(quantity):
                deck.sideboard.append(card)
            return

        print(f"Did not find a valid card in this line: {name} - {data}")

    def build_id_deck_from_edhr(self, edhr_data) -> None:
        deck = self.deck
        if not edhr_data or "archidekt" not in edhr_data:
            deck.error = True
            return

        deck.title = edhr_data["header"] if "header" in edhr_data else None

        for card in edhr_data["archidekt"]:
            card_name = card.get("n")
            if card["c"] == "c":
                deck.commander = Card(
                    deck.session,
                    deck.args,
                    card_id=card["u"],
                    card_name=card_name,
                    is_commander=True,
                    is_thin=True,
                )
            elif card["c"] == "m":
                for _ in range(card["q"]):
                    deck.mainboard.append(
                        Card(
                            deck.session,
                            deck.args,
                            card_id=card["u"],
                            card_name=card_name,
                            is_thin=True,
                        )
                    )
            else:
                print(f"found something weird: {card}")

        if edhr_data.get("panels", {}).get("piechart", {}).get("content"):
            deck.chart = Chart(edhr_data.get("panels", {}).get("piechart", {}).get("content"))

        deck.mana_curve = edhr_data.get("panels", {}).get("mana_curve") or {}
        taglinks = edhr_data.get("panels", {}).get("taglinks") or []
        deck.taglinks = taglinks
        if taglinks:
            deck.popular_tag = taglinks[0].get("slug")

        if deck.commander is None or len(deck.mainboard) < 80:
            deck.error = True