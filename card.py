import requests
import time

import scryfall_bulk

# pylint: disable=missing-function-docstring, missing-class-docstring

# Slightly longer default delay between Scryfall requests to stay under rate limits
SCRYFALL_SLEEP = 0.15

def _get_with_retry(session, url, params=None, timeout=5, max_retries=5):
    """GET with exponential backoff on 429 Too Many Requests. Returns (response, None) or (None, exception)."""
    last_err = None
    for attempt in range(max_retries):
        try:
            response = session.get(url, timeout=timeout, params=params)
            if response.status_code == 429:
                # Exponential backoff: 2, 4, 8, 16, 32 seconds
                wait = 2 ** (attempt + 1)
                if attempt < max_retries - 1:
                    time.sleep(wait)
                    continue
            response.raise_for_status()
            return response, None
        except requests.exceptions.RequestException as err:
            last_err = err
            if getattr(err, "response", None) and getattr(err.response, "status_code", None) == 429:
                wait = 2 ** (attempt + 1)
                if attempt < max_retries - 1:
                    time.sleep(wait)
                    continue
            return None, last_err
    return None, last_err

class Card:
    def __init__(self, session, args, card_id=None, card_name=None, is_commander=False, is_thin=False):
        self.is_commander = is_commander
        self.card_id = card_id
        self.card_name = card_name  # when set (e.g. from EDHREC), bulk lookups use name for oracle_cards
        self.name = None
        self.type_line = None
        self.mana_cost = None
        self.oracle_text = None
        self.rarity = None
        self.price = None
        self.cmc = None
        self.card_art = None
        self.card_pic = None
        self.colors = None
        self.color_identity = None
        self.artist = None
        self.session = session
        self.owned = False
        self.error = False
        self.args = args
        self.thin = is_thin
        self.game_changer = False
        self.edhrec_rank = None
        self.alternate_names = set()

    def __str__(self):
        card_info = f"{self.name} \n"
        card_info += f"Price: {self.price}\n"
        if self.owned:
            card_info += "Is in stock\n"
        if self.is_commander:
            card_info += f"Mana Cost: {self.mana_cost}\n"
            card_info += f"Type Line: {self.type_line}\n"
            card_info += f"Text: {self.oracle_text}\n"
            card_info += f"Rarity: {self.rarity} - "
            card_info += f"CMC: {self.cmc} - "
            card_info += f"Colors: {self.colors} - "
            card_info += f"Color Identity: {self.color_identity}\n"
            card_info += f"Artist: {self.artist}\n"
        return card_info
    
    def search_card(self, card_name, fetch_alternate_names=False):
        scryfall_bulk.ensure_loaded(self.session)
        card_found = scryfall_bulk.get_card_by_name(card_name)
        if card_found is not None:
            self.parse_scryfall_card(card_found)
            if fetch_alternate_names and not self.error:
                self.fetch_alternate_names(card_found)
            return
        url = "https://api.scryfall.com/cards/named"
        params = {"exact": card_name}
        response, err = _get_with_retry(self.session, url, params=params, timeout=5)
        if err:
            print(f"Error looking up scryfall card '{card_name}': {err}")
            self.error = True
            return
        time.sleep(SCRYFALL_SLEEP)
        card_found = response.json()
        self.parse_scryfall_card(card_found)
        if fetch_alternate_names and not self.error:
            self.fetch_alternate_names(card_found)

    def fetch_alternate_names(self, card_data):
        if 'prints_search_uri' not in card_data:
            return
        response, err = _get_with_retry(self.session, card_data['prints_search_uri'], timeout=5)
        if err:
            print(f"Error looking up alternate names for '{self.name}': {err}")
            return
        time.sleep(SCRYFALL_SLEEP)
        prints = response.json()
        for printing in prints.get('data', []):
            self.alternate_names.add(printing['name'])

    def get_data(self):
        if not self.thin:
            return
        if not self.card_id and not self.card_name:
            return
        scryfall_bulk.ensure_loaded(self.session)
        data = None
        if self.card_name:
            data = scryfall_bulk.get_card_by_name(self.card_name)
        if data is None and self.card_id:
            data = scryfall_bulk.get_card_by_id(self.card_id)
        if data is not None:
            self.parse_scryfall_card(data)
            return
        if not self.card_id:
            self.error = True
            return
        url = f"https://api.scryfall.com/cards/{self.card_id}"
        response, err = _get_with_retry(self.session, url, timeout=5)
        if err:
            print(f"Error looking up scryfall card by id: {err}")
            self.error = True
            return
        time.sleep(SCRYFALL_SLEEP)
        data = response.json()
        self.parse_scryfall_card(data)

    def parse_scryfall_card(self, data):
        if data is None:
            self.error = True
            return
        self.name = data["name"] if "name" in data else None
        self.price = self.get_price(data)
        self.game_changer = data.get("game_changer")
        if self.is_commander:
            self.edhrec_rank = data.get("edhrec_rank")
            self.cmc = data["cmc"] if "cmc" in data else None
            self.mana_cost = data["mana_cost"] if "mana_cost" in data else None
            self.type_line = data["type_line"] if "type_line" in data else None
            self.oracle_text = data["oracle_text"] if "oracle_text" in data else None
            self.colors = data["colors"] if "colors" in data else None
            self.color_identity = self.get_color_identity(data["color_identity"]) if "color_identity" in data else None
            self.artist = data["artist"] if "artist" in data else None
            self.rarity = data["rarity"] if "rarity" in data else None
            if data.get("image_uris"):
                self.card_pic = data.get("image_uris", {}).get("normal") or data.get("image_uris", {}).get("small")
        
        if self.name is None or self.price is None or self.name == "":
            self.error = True
        
        self.thin = False
    
    def get_color_identity(self, color_string):
        col_identity = ""
        col_identity += self.check_color(color_string, "W", "🔲")
        col_identity += self.check_color(color_string, "U", "🟦")
        col_identity += self.check_color(color_string, "B", "⬛")
        col_identity += self.check_color(color_string, "R", "🟥")
        col_identity += self.check_color(color_string, "G", "🟩")
        return col_identity
    
    def check_color(self, color_string: str, char, output):
        if char in color_string:
            return output
        else:
            return "X"


    def get_price(self, data):
        if data.get("type_line") is not None and "Basic Land" in str(data["type_line"]):
            return 0.0
        if "prices" in data:
            if "usd" in data["prices"] and data["prices"]["usd"] is not None:
                return float(data["prices"]["usd"])
            elif "usd_foil" in data["prices"] and data["prices"]["usd_foil"] is not None:
                return float(data["prices"]["usd_foil"])
        return 0.0
