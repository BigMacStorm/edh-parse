import requests
import time

class Card:
    def __init__(self, card_id: str, session, is_commander=False):
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

    def get_data(self):
        url = f"https://api.scryfall.com/cards/{self.card_id}"
        data = None
        try:
            response = self.session.get(url, timeout=2)
            response.raise_for_status()
            if not response.from_cache:
                time.sleep(0.1)
            data = response.json()

        except requests.exceptions.RequestException as err:
            print(f"Error looking up scryfall card: {err}")
        
        self.name = data["name"] if "name" in data else None
        self.price = self.get_price(data)
        if self.is_commander:
            self.cmc = data["cmc"] if "cmc" in data else None
            self.mana_cost = data["mana_cost"] if "mana_cost" in data else None
            self.type_line = data["type_line"] if "type_line" in data else None
            self.oracle_text = data["oracle_text"] if "oracle_text" in data else None
            self.colors = data["colors"] if "colors" in data else None
            self.color_identity = data["color_identity"] if "color_identity" in data else None
            self.artist = data["artist"] if "artist" in data else None
            self.rarity = data["rarity"] if "rarity" in data else None
        
        if self.name is None or self.price is None or self.name == "":
            self.error = True

    def get_price(self, data):
        if data["type_line"] is not None and "Basic Land" in str(data["type_line"]):
            return 0.0
        if "prices" in data:
            if "usd" in data["prices"] and data["prices"]["usd"] is not None:
                return float(data["prices"]["usd"])
            elif "usd_foil" in data["prices"] and data["prices"]["usd_foil"] is not None:
                return float(data["prices"]["usd_foil"])
        return 0.0
