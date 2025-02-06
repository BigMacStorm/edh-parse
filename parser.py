import argparse
from pathlib import Path
import requests
import json
import re
import time
from deck import Deck

def build_args():
    parser = argparse.ArgumentParser()
    edh_group = parser.add_mutually_exclusive_group()
    edh_group.add_argument("-c", "--commander", help="The name of the commander to get from EDHREC.")
    edh_group.add_argument("-cl", "--list", help="A list of edhrec URLs to load data from.")
    return parser.parse_args()

def lookup_commander(commander_name: str):
    formatted_name = get_edhrec_name(commander_name)
    url = f"https://json.edhrec.com/pages/commanders/{formatted_name}.json"
    print(url)

    # 100ms delay per call to avoid abusing edhrec API and getting rate limited.
    time.sleep(0.1)
    try:
        response = requests.get(url, timeout=2)
        response.raise_for_status()
        edhr_data = response.json()
        return edhr_data

    except requests.exceptions.RequestException as err:
        print(f"Error looking up edhrec commander: {err}")

def get_edhrec_name(commander_name: str):
    regex = r"[^\w\s]"
    stripped_name = re.sub(regex, "", commander_name)
    formatted_name = stripped_name.lower().replace(" ", "-")
    return formatted_name

def build_thin_deck(edhr_data):
    mainboard_ids = []
    commander_id = ""
    for card in edhr_data["archidekt"]:
        if card["c"] == "c":
            commander_id = card["u"]
        elif card["c"] == "m":
            for _ in range(card["q"]):
                mainboard_ids.append(card["u"])
        else:
            print(f"found something weird: {card}")
    if commander_id == "" or len(mainboard_ids) != 99:
        raise ValueError("Did not find the correct number of cards")
    return Deck(commander_id, mainboard_ids)

def main():
    args = build_args()

    if(args.list):
        print(args.list)
    if(args.commander):
        edhr_data = lookup_commander(args.commander)
        deck = build_thin_deck(edhr_data)
        print(deck.commander.card_id)
        print(len(deck.mainboard))

if __name__ == "__main__":
    main()