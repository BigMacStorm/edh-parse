import time

from utils.http_retry import get_with_retry as _http_get_with_retry
from clients.scryfall_client import ScryfallClient

# pylint: disable=missing-function-docstring, missing-class-docstring

# Slightly longer default delay between Scryfall requests to stay under rate limits
SCRYFALL_SLEEP = 0.15

def _get_with_retry(session, url, params=None, timeout=5, max_retries=5):
    """Backwards-compatible wrapper for Scryfall lookups."""
    return _http_get_with_retry(
        session,
        url,
        params=params,
        timeout=timeout,
        max_retries=max_retries,
    )

class Card:
    def __init__(self, session, args, card_id=None, card_name=None, is_commander=False, is_thin=False):
        self.is_commander = is_commander
        self.card_id = card_id
        self.card_name = card_name  # when set (e.g. from EDHREC), bulk lookups use name for oracle_cards
        # Core identity / pricing
        self.name = None
        self.type_line = None
        self.mana_cost = None
        self.oracle_text = None
        self.rarity = None
        self.price = None
        self.cmc = None
        self.card_art = None
        self.card_pic = None
        # Color / identity
        self.colors = None
        self.color_identity = None
        # Artist / printing metadata
        self.artist = None
        self.set_code = None
        self.set_name = None
        self.released_at = None  # ISO date string from Scryfall, e.g. "2025-11-08"
        self.set_type = None  # e.g. 'expansion', 'core', 'commander', 'promo', 'secret_lair'
        self.is_reprint = False  # Scryfall 'reprint' flag for this printing
        # EDHREC metadata (optional; used by --new-cards-html)
        self.edh_inclusion = None  # string like "4.2%"
        self.edh_synergy = None    # string like "7.0%"
        self.session = session
        self.owned = False
        self.error = False
        self.args = args
        self.thin = is_thin
        self.game_changer = False
        self.edhrec_rank = None
        self.alternate_names = set()
        self.scryfall_client = ScryfallClient(self.session, sleep_seconds=SCRYFALL_SLEEP)

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
        card_found, err = self.scryfall_client.get_card_by_name_exact(card_name, timeout=5, max_retries=5)
        if err:
            print(f"Error looking up scryfall card '{card_name}': {err}")
            self.error = True
            return

        self.parse_scryfall_card(card_found)
        if fetch_alternate_names and not self.error:
            self.fetch_alternate_names(card_found)

    def fetch_alternate_names(self, card_data):
        alt_names, err = self.scryfall_client.fetch_alternate_names(card_data, timeout=5, max_retries=5)
        if err:
            print(f"Error looking up alternate names for '{self.name}': {err}")
            return
        if alt_names:
            self.alternate_names.update(alt_names)

    def get_data(self):
        if not self.thin:
            return
        if not self.card_id and not self.card_name:
            return
        data = None
        err = None
        if self.card_name:
            data, err = self.scryfall_client.get_card_by_name_exact(self.card_name, timeout=5, max_retries=5)
            if err:
                print(f"Error looking up scryfall card '{self.card_name}': {err}")
                self.error = True
                return

        if data is None and self.card_id:
            data, err = self.scryfall_client.get_card_by_id(self.card_id, timeout=5, max_retries=5)
            if err:
                print(f"Error looking up scryfall card by id: {err}")
                self.error = True
                return

        if data is None:
            if not self.card_id:
                self.error = True
            else:
                self.error = True
            return

        self.parse_scryfall_card(data)

    def parse_scryfall_card(self, data):
        if data is None:
            self.error = True
            return
        # Common fields for all cards (not just commanders)
        self.name = data.get("name")
        self.price = self.get_price(data)
        self.game_changer = data.get("game_changer")
        self.type_line = data.get("type_line")
        self.oracle_text = data.get("oracle_text")
        self.colors = data.get("colors")
        if "color_identity" in data:
            self.color_identity = self.get_color_identity(data["color_identity"])
        self.artist = data.get("artist")
        self.rarity = data.get("rarity")
        self.set_code = data.get("set")
        self.set_name = data.get("set_name")
        self.released_at = data.get("released_at")
        self.set_type = data.get("set_type")
        self.is_reprint = bool(data.get("reprint", False))
        # MDFC / transform: use first face for image and combine oracle text
        if data.get("card_faces"):
            first = data["card_faces"][0]
            if first.get("image_uris"):
                self.card_pic = first.get("image_uris", {}).get("normal") or first.get("image_uris", {}).get("small")
            texts = [f.get("oracle_text") for f in data["card_faces"] if f.get("oracle_text")]
            if texts:
                self.oracle_text = "\n\n".join(texts)
        # Prefer normal-size art, fall back to small; also works for non-commander cards
        if self.card_pic is None and data.get("image_uris"):
            self.card_pic = data.get("image_uris", {}).get("normal") or data.get("image_uris", {}).get("small")

        # Extra commander-only fields
        if self.is_commander:
            self.edhrec_rank = data.get("edhrec_rank")
            self.cmc = data.get("cmc")
            self.mana_cost = data.get("mana_cost")
        
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
