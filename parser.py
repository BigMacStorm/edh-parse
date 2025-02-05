import argparse
from pathlib import Path
import requests
import json

def main():
    args = buildArgs()

    if(args.list):
        print(args.list)
    if(args.commander):
        searchForCommander(args.commander)

def buildArgs():
    parser = argparse.ArgumentParser()
    edhGroup = parser.add_mutually_exclusive_group()
    edhGroup.add_argument("-c", "--commander", help="The name of the commander to get from EDHREC.")
    edhGroup.add_argument("-cl", "--list", help="A list of edhrec URLs to load data from.")
    return parser.parse_args()

def searchForCommander(commanderName: str):
    url = "https://api.scryfall.com/cards/search"
    params = {
        "q": commanderName
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        sfdata = response.json()
        print(sfdata)
    
    except requests.exceptions.RequestException as err:
        print(f"Error looking up scryfall commander: {err}")


if __name__ == "__main__":
    main()