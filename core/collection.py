from pathlib import Path
import math
import sys
from urllib.parse import quote
import requests_cache
from utils.progress_utils import progress_bar
import requests
import json
import re
import time
from core.deck import Deck, Cost, _edhrec_get_with_retry, EDHREC_SLEEP
from itertools import chain
from collections import defaultdict, Counter
import csv
from core.card import _get_with_retry, SCRYFALL_SLEEP
from clients.edhrec_client import EdhrecClient
from utils.html_render_utils import _canonical_color_identity, _escape, _svg_pie, _svg_mana_curve

# pylint: disable=missing-function-docstring, missing-class-docstring

class Collection:
    def __init__(self, args):
        self.decks = []
        self.args = args
        # Reuse Normal page + average-deck data for Budget/Expensive so we don't 2x requests and hit rate limits
        self._edhr_cache = {}
        # Be sure to cache our responses to make sure that we arent making extra calls for no reason. Expire after 31 days for safety.
        self.session = requests_cache.CachedSession('cache/card_cache', expire_after=3600*24*31)
        if self.args.fresh_cache:
            self.session.cache.clear()
        self.edhrec_client = EdhrecClient(self.session, sleep_seconds=EDHREC_SLEEP)

    def add_all_costs(self, just_name: str=None, name_with_type=None, bar=None, bar_value=None):
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
        info = edhr_name or ""
        for cost in [Cost.Normal, Cost.Budget, Cost.Expensive]:
            if bar is not None and bar_value is not None:
                bar.update(bar_value, info=f"Loading thin deck for {info} ({cost.name})")
                sys.stdout.flush()
            self.decks.append(self.new_deck().init_thin_edhr_deck(edhr_name, cost, deck_type=deck_type))
            if bar is not None and bar_value is not None:
                bar.update(bar_value)
                sys.stdout.flush()
    
    def add_list_deck(self, list_cards):
        card_count = len(list_cards)
        with progress_bar(card_count, initial_value=0) as bar:
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
        with progress_bar(not_thin_cards, initial_value=0) as bar:
            for deck in self.decks:
                card_count = deck.lookup_card_data(bar, card_count)
    
    def print_collection(self):
        sorted_decks = sorted(
            (d for d in self.decks if d.commander is not None),
            key=lambda deck: deck.commander.name
        )
        commanders_seen = set()
        for deck in sorted_decks:
            if deck.commander and deck.commander.name not in commanders_seen:
                commanders_seen.add(deck.commander.name)
                print(str(deck.commander))
            print(deck)
    
    def mark_cards_owned(self, manabox_data):
        names = set()
        for row in manabox_data:
            name = row.get("Name") or row.get("Card")
            if name:
                names.add(name)
        for deck in self.decks:
            if deck.commander is None:
                continue
            if deck.commander.name in names:
                deck.commander.owned = True
            for card in chain(deck.mainboard, deck.sideboard):
                 if card.name in names:
                    card.owned = True
                    deck.owned_set.add((card.price, card.name))
    
    def new_deck(self):
        return Deck(self.session, self.args, edhr_cache=self._edhr_cache)
    
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
            if deck.commander is not None:
                output.append(deck.build_dict())
        
        filename = self.args.csv_file if self.args.csv_file else "csv_out.csv"

        try:
            with open(filename, 'w', newline='', encoding='utf-8') as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(output)
        except Exception as e:
            print(f"Error writing to CSV: {e}")

    def _fetch_combos(self, edhr_slug, max_combos=15):
        """Fetch combo list for a commander from EDHREC. Returns list of combo dicts."""
        if not edhr_slug:
            return []
        url = f"https://json.edhrec.com/pages/combos/{edhr_slug}.json"
        data, err = self.edhrec_client.get_json(url, timeout=10)
        if err or not data:
            return []
        container = data.get("container") or {}
        json_dict = container.get("json_dict") or {}
        cardlists = json_dict.get("cardlists") or []
        out = []
        for entry in cardlists[:max_combos]:
            cardviews = entry.get("cardviews") or []
            combo = entry.get("combo") or {}
            cards = [cv.get("name") for cv in cardviews if cv.get("name")]
            results = combo.get("results") or []
            count = combo.get("count") or 0
            max_count = combo.get("maxCount") or 1
            pct = round((count / max_count) * 100, 2) if max_count else 0
            href = entry.get("href") or ""
            combo_url = ("https://edhrec.com" + href) if href.startswith("/") else href
            vote = combo.get("comboVote")
            if vote is None:
                vote = {}
            bracket_raw = vote.get("bracket") if isinstance(vote, dict) else None
            if bracket_raw is None or bracket_raw == "":
                bracket = "Unrated"
            elif str(bracket_raw).lower() == "any":
                bracket = "Any bracket"
            elif str(bracket_raw).isdigit():
                bracket = f"Bracket {bracket_raw}"
            else:
                bracket = f"Bracket {bracket_raw}"
            out.append({
                "cards": cards,
                "results": results,
                "count": count,
                "max_count": max_count,
                "percentage": pct,
                "url": combo_url,
                "bracket": bracket,
            })
        return out

    def write_summary_html(self, path="outputs/summary.html", used_manabox=False):
        """Write a single self-contained summary webpage of all decks."""
        from renderers.summary_renderer import write_summary_html as _render_summary_html

        _render_summary_html(self, path=path, used_manabox=used_manabox)

    def write_latest_set_html(self, path="outputs/latest_set.html"):
        """Write a webpage highlighting cards from the most recent set across all decks."""
        # Delegate rendering to a dedicated module to keep Collection focused on
        # deck-building/orchestration rather than massive HTML string assembly.
        from renderers.latest_set_renderer import write_latest_set_html as _render_latest_set_html

        _render_latest_set_html(
            decks=self.decks,
            session=self.session,
            path=path,
            get_with_retry_fn=_get_with_retry,
            scryfall_sleep=SCRYFALL_SLEEP,
        )
        return
