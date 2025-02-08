import argparse
from pathlib import Path
import requests
import json
import re
import time
from deck import Deck
from collection import Collection
import csv

# pylint: disable=missing-function-docstring, missing-class-docstring

def build_args():
    parser = argparse.ArgumentParser()
    edh_group = parser.add_mutually_exclusive_group()
    edh_group.add_argument("-c", "--commander", help="The name of the commander to get from EDHREC.")
    edh_group.add_argument("-cl", "--list", help="A list of edhrec URLs to load data from.")
    parser.add_argument("-mb", "--manabox", help="An exported Manabox CSV file to indicate which cards are owned already.")
    return parser.parse_args()

def main():
    args = build_args()
    commander_urls = []
    
    collection = Collection()
    if(args.list):
        commander_urls = load_urls_from_file(args.list)
        for commander_url in commander_urls:
            collection.add_all_costs(commander_url)
    if(args.commander):
        collection.add_all_costs(get_edhrec_name(args.commander))
        

    collection.lookup_deck_data()

    if(args.manabox):
        collection.mark_cards_owned(load_manabox(args.manabox))

    collection.print_collection()

def get_edhrec_name(commander_name: str):
    regex = r"[^\w\s\-]"
    stripped_name = re.sub(regex, "", commander_name)
    formatted_name = stripped_name.lower().replace(" ", "-")
    return formatted_name

def load_manabox(filename):
    try:
        with open(filename, 'r', encoding='utf-8') as csvfile:
            reader = csv.reader(csvfile, delimiter=",", quotechar="\"")

            header_row = next(reader)
            data = []
            for row in reader:
                row_data = {}
                for i, cell in enumerate(row):
                    if i < len(header_row):
                        row_data[header_row[i]] = cell
                data.append(row_data)
            return data

    except FileNotFoundError:
        print(f"Error: File not found at {filename}")
        return None
    except Exception as e:
        print(f"An error occurred while reading the file: {e}")
        return None
    
def load_urls_from_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as file:
            names = []
            for line in file:
                url = line.strip()
                if url and isinstance(url, str):
                    url = url.lower()
                    url.replace("/budget", "")
                    url.replace("/expensive", "")
                    match = re.search(r"/commanders/([^/]+)(?:/([^/]+))?", url)  # Two capture groups
                    if match:
                        commander = match.group(1)
                        deck_type = match.group(2) if match.group(2) else None  # Handle optional deck_type
                        commander_url = commander
                        if deck_type:
                            commander_url += f"/{deck_type}"
                        names.append(commander_url)
            return sorted(list(set(names)))

    except FileNotFoundError:
        print(f"Error: File not found at {filepath}")
        return None
    except Exception as e:  # Catch other potential errors
        print(f"An error occurred while reading the file: {e}")
        return None

if __name__ == "__main__":
    main()