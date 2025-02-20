import requests
import time

# pylint: disable=missing-function-docstring, missing-class-docstring

class Card:
    def __init__(self, session, args, card_id=None, is_commander=False, is_thin=False):
        self.is_commander = is_commander
        self.card_id = card_id
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
    
    def search_card(self, card_name):
        url = "https://api.scryfall.com/cards/search"
        query = {"q": card_name}
        data = None
        card_found = None
        try:
            response = self.session.get(url, timeout=5, params=query)
            response.raise_for_status()
            if not response.from_cache:
                time.sleep(0.1)
            data = response.json()

        except requests.exceptions.RequestException as err:
            print(f"Error looking up scryfall card by name: {err}")
        index = 0
        for x in range(len(data["data"])):
            if data["data"][x]["name"] == card_name:
                index = x
                break
        card_found = data["data"][index]
        self.parse_scryfall_card(card_found)

    def get_data(self):
        if not self.thin:
            return
        if not self.card_id:
            return
        url = f"https://api.scryfall.com/cards/{self.card_id}"
        data = None
        try:
            response = self.session.get(url, timeout=2)
            response.raise_for_status()
            if not response.from_cache:
                time.sleep(0.1)
            data = response.json()

        except requests.exceptions.RequestException as err:
            print(f"Error looking up scryfall card by id: {err}")

        self.parse_scryfall_card(data)

    def parse_scryfall_card(self, data):
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
            self.card_pic = data.get("image_uris", {}).get("small")
        
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
        if data["type_line"] is not None and "Basic Land" in str(data["type_line"]):
            return 0.0
        if "prices" in data:
            if "usd" in data["prices"] and data["prices"]["usd"] is not None:
                return float(data["prices"]["usd"])
            elif "usd_foil" in data["prices"] and data["prices"]["usd_foil"] is not None:
                return float(data["prices"]["usd_foil"])
        return 0.0
