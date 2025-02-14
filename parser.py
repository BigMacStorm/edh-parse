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
    edh_group.add_argument("-cl", "--commander_list", help="A list of edhrec URLs to load data from.")
    parser.add_argument("-mb", "--manabox", help="An exported Manabox CSV file to indicate which cards are owned already.")
    parser.add_argument("-l", "--list", help="A list of cards in MTGO format")
    parser.add_argument("-o", "--show_owned", action="store_true", help="Output the list of owned cards found.")
    parser.add_argument("-csv", "--csv", action="store_true", help="Output the collection to a CSV to be viewed")
    parser.add_argument("-cf", "--csv_file", help="Where to write the CSV file. If not provided, default will be used.")
    return parser.parse_args()

def main():
    args = build_args()
    commander_urls = []
    
    collection = Collection(args)
    if(args.commander_list):
        commander_urls = load_urls_from_file(args.commander_list)
        for commander_url in commander_urls:
            collection.add_all_costs(commander_url)
    if(args.commander):
        collection.add_all_costs(get_edhrec_name(args.commander))

    if(args.list):
        list_cards = parse_card_list(args.list)
        collection.add_list_deck(list_cards)

    collection.lookup_deck_data()

    if(args.manabox):
        collection.mark_cards_owned(load_manabox(args.manabox))

    collection.print_collection()
    if(args.csv):
        collection.write_to_file()

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

def parse_card_list(filename):
    try:
        with open(filename, 'r') as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"Error: File '{filename}' not found.")
        return {}

    card_data = {}
    current_header = "mainboard"
    while True:
        if lines:
            last_line = lines[-1].strip()
            if last_line:
                match = re.match(r"(\d+)\s+(.+)", last_line)
                if match:
                    commander_quantity = int(match.group(1))
                    commander_name = match.group(2).strip()
                else:
                    print(f"Warning: Could not parse commander line: {last_line}")
                    commander_name = None
                    commander_quantity = 1

                if commander_name:
                    card_data[commander_name] = {"quantity": commander_quantity, "header": "commander"}
                lines = lines[:-1]  # Remove the last line from the list for normal processing
                break
            
            lines = lines[:-1] # Remove the last line if it's whitespace, then loop until we find something

    for line in lines:
        line = line.strip()

        if line.upper().startswith("SIDEBOARD:"):
            current_header = "sideboard"
            continue
        elif not line:
            continue

        match = re.match(r"(\d+)\s+(.+)", line)
        if match:
            quantity = int(match.group(1))
            card_name = match.group(2).strip()
            card_data[card_name] = {"quantity": quantity, "header": current_header}
        else:
            print(f"Warning: Could not parse line: {line}")

    return card_data

if __name__ == "__main__":
    main()