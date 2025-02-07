import argparse
from pathlib import Path
import requests
import json
import re
import time
from deck import Deck
from collection import Collection

def build_args():
    parser = argparse.ArgumentParser()
    edh_group = parser.add_mutually_exclusive_group()
    edh_group.add_argument("-c", "--commander", help="The name of the commander to get from EDHREC.")
    edh_group.add_argument("-cl", "--list", help="A list of edhrec URLs to load data from.")
    return parser.parse_args()

def main():
    args = build_args()
    names = []

    if(args.list):
        print(args.list)
    if(args.commander):
        names.append(args.commander)
    
    collection = Collection()
    for name in names:
        collection.add_all_costs(get_edhrec_name(name))
    collection.lookup_deck_data()
    for deck in collection.decks:
        print(deck)

def get_edhrec_name(commander_name: str):
    regex = r"[^\w\s]"
    stripped_name = re.sub(regex, "", commander_name)
    formatted_name = stripped_name.lower().replace(" ", "-")
    return formatted_name

if __name__ == "__main__":
    main()