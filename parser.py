import requests_cache
import argparse
from pathlib import Path
import re
import sys
import time
import csv
from collections import Counter
import requests
from progress_utils import progress_bar
from deck import Deck
from collection import Collection
from deck_diff import diff_decks, generate_shopping_list
from card import Card
import scryfall_bulk

# pylint: disable=missing-function-docstring, missing-class-docstring

def build_args():
    parser = argparse.ArgumentParser()
    edh_group = parser.add_mutually_exclusive_group()
    edh_group.add_argument("-c", "--commander", help="The name of the commander to get from EDHREC.")
    edh_group.add_argument("-cl", "--commander_list", help="A list of edhrec URLs to load data from.")
    parser.add_argument("-d", "--diff", help="A directory containing deck files to diff (e.g., my_deck_old, my_deck_new).")
    parser.add_argument("-mb", "--manabox", help="An exported Manabox CSV file to indicate which cards are owned already.")
    parser.add_argument("-l", "--list", help="A list of cards in MTGO format")
    parser.add_argument("--locate", help="Text file with card names ('1 Card Name' per line) to locate in stock binders.")
    parser.add_argument("-o", "--show_owned", action="store_true", help="Output the list of owned cards found.")
    parser.add_argument("-n", "--need", action="store_true", help="Output the list of cards still needed to buy.")
    parser.add_argument("-csv", "--csv", action="store_true", help="Output the collection to a CSV to be viewed")
    parser.add_argument("-cf", "--csv_file", help="Where to write the CSV file. If not provided, default will be used.")
    parser.add_argument("-html", "--html", nargs="?", const="summary.html", metavar="FILE", help="Write a summary webpage. Default: summary.html")
    parser.add_argument("-all", "--all", action="store_true", help="Show all cards when printing deck.")
    parser.add_argument("-fc", "--fresh_cache", action="store_true", help="To force a cache clear on lookups.")
    return parser.parse_args()

def main():
    args = build_args()

    if args.diff:
        if not args.manabox:
            print("Error: The --diff feature requires a ManaBox CSV file specified with --manabox.")
            return

        diff_dir = Path(args.diff)
        if not diff_dir.is_dir():
            print(f"Error: --diff argument '{args.diff}' is not a valid directory.")
            return

        # Find file pairs
        old_files = {p.stem.replace('_old', ''): p for p in diff_dir.glob('*_old')}
        new_files = {p.stem.replace('_new', ''): p for p in diff_dir.glob('*_new')}
        
        pairs = []
        for name, old_path in old_files.items():
            if name in new_files:
                pairs.append((old_path, new_files[name]))

        if not pairs:
            print(f"No matching '_old' and '_new' file pairs found in '{args.diff}'.")
            return

        aggregated_needed = Counter()
        diff_results = []

        # First pass: Get diffs and aggregate
        print("Analyzing deck pairs...")
        for old_path, new_path in pairs:
            try:
                old_deck = parse_deck_file(old_path)
                new_deck = parse_deck_file(new_path)
                added, removed = diff_decks(old_deck, new_deck)
                
                diff_results.append({
                    'name': old_path.stem.replace('_old', ''),
                    'added': added,
                    'removed': removed
                })
                aggregated_needed.update(added)
            except Exception as e:
                print(f"\nError processing pair {old_path.name}/{new_path.name}: {e}")

        # --- Data Fetching ---
        print("\nFetching card data...")
        owned_quantities = load_manabox(args.manabox, as_dict=True)
        session = requests_cache.CachedSession('card_cache', expire_after=3600*24*31)
        if args.fresh_cache:
            session.cache.clear()

        name_to_canonical = {}
        canonical_to_price = {}

        # Look up only needed cards and their alternates
        needed_card_names = set(aggregated_needed.keys())
        with progress_bar(len(needed_card_names)) as bar:
            for i, name in enumerate(needed_card_names):
                bar.update(i, info=f"Fetching card data for {name}")
                if name not in name_to_canonical:
                    card = Card(session, args)
                    card.search_card(name, fetch_alternate_names=True)
                    if not card.error:
                        all_names_for_card = {name} | card.alternate_names
                        for n in all_names_for_card:
                            name_to_canonical[n] = card.name
                        if card.name not in canonical_to_price:
                            canonical_to_price[card.name] = card.price
        
        canonical_owned = Counter()
        for name, qty in owned_quantities.items():
            if name in name_to_canonical:
                canonical_name = name_to_canonical[name]
                canonical_owned[canonical_name] += qty
        
        # --- Printing Results ---
        for result in diff_results:
            print(f"\n--- Diff for {result['name']} ---")
            print("Cards Added:")
            for name, qty in result['added'].items():
                print(f"  {qty}x {name}")
            print("Cards Removed:")
            for name, qty in result['removed'].items():
                print(f"  {qty}x {name}")

            # Calculations for recap
            canonical_added = Counter()
            for name, qty in result['added'].items():
                if name in name_to_canonical:
                    canonical_name = name_to_canonical[name]
                    canonical_added[canonical_name] += qty

            _, _, to_buy_deck = generate_shopping_list(canonical_added, canonical_owned)

            total_cost_new = sum(canonical_to_price.get(name, 0.0) * qty for name, qty in canonical_added.items())
            cost_to_buy = sum(canonical_to_price.get(name, 0.0) * qty for name, qty in to_buy_deck.items())

            print("\nRecap:")
            print(f"  Cards to replace: {sum(result['added'].values())}")
            print(f"  Total cost of new cards: ${total_cost_new:.2f}")
            print(f"  Cost factoring in owned cards: ${cost_to_buy:.2f}\n")

        # --- Overall Shopping List ---
        print("\n--- Overall Shopping List ---")
        canonical_needed = Counter()
        for name, qty in aggregated_needed.items():
            if name in name_to_canonical:
                canonical_name = name_to_canonical[name]
                canonical_needed[canonical_name] += qty

        all_needed, owned_from_needed, to_buy = generate_shopping_list(canonical_needed, canonical_owned)

        all_needed_priced = {name: {'quantity': qty, 'price': canonical_to_price.get(name, 0.0)} for name, qty in all_needed.items()}
        owned_from_needed_priced = {name: {'quantity': qty, 'price': canonical_to_price.get(name, 0.0)} for name, qty in owned_from_needed.items()}
        to_buy_priced = {name: {'quantity': qty, 'price': canonical_to_price.get(name, 0.0)} for name, qty in to_buy.items()}

        print_list_with_totals("All Cards to be Added", all_needed_priced)
        print_list_with_totals("Cards Owned", owned_from_needed_priced)
        print_list_with_totals("Cards to Buy", to_buy_priced)
        
        return # Exit after diff processing

    # --- Locate cards in stock binders mode ---
    if args.locate:
        if not args.manabox:
            print("Error: The --locate feature requires a stock/Manabox CSV file specified with --manabox.")
            return

        locate_file = Path(args.locate)
        if not locate_file.is_file():
            print(f"Error: --locate argument '{args.locate}' is not a valid file.")
            return

        card_names = parse_card_names_file(locate_file)
        if not card_names:
            print("No card names found to locate.")
            return

        stock_rows = load_manabox(args.manabox)
        if stock_rows is None:
            return

        # Use Scryfall bulk data to get rarities
        session = requests_cache.CachedSession('card_cache', expire_after=3600*24*31)
        if args.fresh_cache:
            session.cache.clear()
        scryfall_bulk.ensure_loaded(session)

        locate_cards_in_stock(card_names, stock_rows, session)
        return

    # --- Existing Functionality ---
    collection = Collection(args)
    session = requests.Session()
    if(args.commander_list):
        names_and_types = load_urls_from_file(args.commander_list)
        if names_and_types is None:
            return
        # Eager-load Scryfall bulk so the first commander that needs a name lookup doesn't pause ~15s mid-loop
        if not scryfall_bulk.is_available():
            print("Loading Scryfall bulk data...", flush=True)
            scryfall_bulk.ensure_loaded(session)
        with progress_bar(len(names_and_types), initial_value=0) as bar:
            count = 0
            for name_and_type in names_and_types:
                info = name_and_type[0]
                if name_and_type[1]:
                    info += f" - {name_and_type[1]}"
                bar.update(count, info=f"Loading thin deck for {info}")
                sys.stdout.flush()
                collection.add_all_costs(name_with_type=name_and_type, bar=bar, bar_value=count)
                count += 1
    if(args.commander):
        collection.add_all_costs(just_name=get_edhrec_name(args.commander))

    if(args.list):
        list_cards = parse_card_list(args.list)
        collection.add_list_deck(list_cards)

    collection.lookup_deck_data()

    manabox_data = load_manabox(args.manabox) if args.manabox else None
    if manabox_data is not None:
        collection.mark_cards_owned(manabox_data)

    collection.print_collection()
    if args.csv:
        collection.write_to_file()
    if args.html is not None:
        collection.write_summary_html(args.html, used_manabox=(manabox_data is not None))

def print_list_with_totals(title: str, card_dict: dict):
    """Prints a list of cards with their prices and a total value."""
    print(f"\n--- {title} ---")
    total_value = 0.0
    
    # Sort by price, descending
    sorted_cards = sorted(card_dict.items(), key=lambda item: item[1].get('price', 0.0), reverse=True)

    for name, data in sorted_cards:
        qty = data['quantity']
        price = data.get('price', 0.0)
        total_card_price = qty * price
        total_value += total_card_price
        print(f"{qty}x {name}")
    print(f"\nTotal Value for {title}: ${total_value:.2f}")


def parse_card_names_file(filepath: Path):
    """Parse a simple text file of card names, accepting optional MTGO-style counts ('1 Card Name')."""
    names = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = re.match(r"(\\d+)\\s+(.+)", line)
            if m:
                names.append(m.group(2).strip())
            else:
                names.append(line)
    return names


def locate_cards_in_stock(card_names, stock_rows, session):
    """
    Given a list of card names and raw stock rows from load_manabox(as_dict=False),
    group results by binder (Stock/Binder/Secret) and print cards under each,
    including rarity from Scryfall bulk data.
    """
    # Build index: lowercased card name -> binder -> total quantity
    index = {}
    for row in stock_rows:
        name = (row.get("Name") or row.get("Card") or "").strip()
        if not name:
            continue
        binder = (
            row.get("Binder Name")
            or row.get("Binder")
            or row.get("Folder")
            or ""
        ).strip()
        if not binder:
            continue
        qty_str = (
            row.get("Quantity")
            or row.get("Qty")
            or row.get("Qty.")
            or ""
        ).strip()
        try:
            qty = int(qty_str) if qty_str else 1
        except ValueError:
            qty = 1
        key = name.lower()
        if key not in index:
            index[key] = {}
        index[key][binder] = index[key].get(binder, 0) + qty

    # Helper to get rarity code from Scryfall bulk once per name
    rarity_cache = {}

    def rarity_code(card_name: str) -> str:
        if card_name in rarity_cache:
            return rarity_cache[card_name]
        data = scryfall_bulk.get_card_by_name(card_name)
        code = "?"
        if data is not None:
            r = (data.get("rarity") or "").strip().lower()
            if r:
                # common/uncommon/rare/mythic/etc -> C/U/R/M/...
                code = r[0].upper()
        rarity_cache[card_name] = code
        return code

    # Binder priority: secret binder > binder > stock
    def binder_priority(name: str) -> int:
        n = (name or "").strip().lower()
        if n in ("secret binder", "secret"):
            return 0
        if n == "binder":
            return 1
        if n == "stock":
            return 2
        return 3

    # Build binder -> list of (card_name, qty, rarity), honoring priority so each card
    # appears in at most one binder: first in Secret Binder, else Binder, else Stock.
    binder_groups = {}
    missing = []
    for name in card_names:
        key = name.lower()
        binders = index.get(key)
        if not binders:
            missing.append(name)
            continue
        # Choose the highest-priority binder this card appears in
        chosen_binder = None
        chosen_qty = 0
        best_prio = 10
        for binder, qty in binders.items():
            pr = binder_priority(binder)
            if pr < best_prio:
                best_prio = pr
                chosen_binder = binder
                chosen_qty = qty
        if chosen_binder is None:
            missing.append(name)
            continue
        rcode = rarity_code(name)
        binder_groups.setdefault(chosen_binder, []).append((name, chosen_qty, rcode))

    # Print grouped by binder in priority order
    for binder in sorted(binder_groups.keys(), key=binder_priority):
        print(binder)
        for name, qty, rcode in sorted(binder_groups[binder], key=lambda t: t[0].lower()):
            print(f"  {qty}x {name} [{rcode}]")

    if missing:
        print("\nNot found in Stock/Binder/Secret binders:")
        for name in missing:
            print(f"  {name}")

def get_edhrec_name(commander_name: str):
    regex = r"[^\w\s\-]"
    stripped_name = re.sub(regex, "", commander_name)
    formatted_name = stripped_name.lower().replace(" ", "-")
    return formatted_name

def parse_deck_file(filepath: Path) -> dict:
    """Parses a decklist file with format '1 Card Name'."""
    deck = Counter()
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            
            match = re.match(r"(\d+)\s+(.+)", line)
            if match:
                quantity = int(match.group(1))
                card_name = match.group(2).strip()
                deck[card_name] += quantity
            else:
                print(f"Warning: Could not parse line in {filepath.name}: {line}")
    return dict(deck)

def _find_csv_col(header_row, *candidates):
    """Return index of first column whose name (stripped, lower) matches one of candidates, else None."""
    norm = {i: (h or "").strip().lower() for i, h in enumerate(header_row)}
    for want in candidates:
        want = want.strip().lower()
        for i, name in norm.items():
            if name == want:
                return i
    return None


def load_manabox(filename, as_dict=False):
    """Load stock/Manabox CSV. Only includes rows in binder named 'binder', 'secret', or 'stock' (case-insensitive)."""
    try:
        with open(filename, 'r', encoding='utf-8') as csvfile:
            reader = csv.reader(csvfile, delimiter=",", quotechar='"')
            header_row = next(reader)
            if not header_row:
                return {} if as_dict else []

            # Only accept cards in these binders
            allowed_binder_names = ["binder", "secret binder", "stock"]
            allowed_lower = [b.lower() for b in allowed_binder_names]

            name_col = _find_csv_col(header_row, "Name", "Card")
            qty_col = _find_csv_col(header_row, "Quantity", "Qty", "Qty.")
            binder_col = _find_csv_col(header_row, "Binder Name", "Binder", "Folder")
            if name_col is None or binder_col is None:
                print("Error: CSV must have a card column ('Name' or 'Card') and a binder column ('Binder Name', 'Binder', or 'Folder').")
                return None
            if qty_col is None and as_dict:
                print("Error: CSV must have 'Quantity' or 'Qty' for diff mode.")
                return None

            if as_dict:
                quantities = {}
                for row in reader:
                    if len(row) <= max(name_col, binder_col):
                        continue
                    binder_name = (row[binder_col] or "").strip().lower()
                    if binder_name not in allowed_lower:
                        continue
                    card_name = (row[name_col] or "").strip()
                    if not card_name:
                        continue
                    qty = 1
                    if qty_col is not None and qty_col < len(row) and row[qty_col].strip().isdigit():
                        qty = int(row[qty_col])
                    quantities[card_name] = quantities.get(card_name, 0) + qty
                return quantities

            binder_header = header_row[binder_col] if binder_col < len(header_row) else ""
            data = []
            for row in reader:
                row_data = {}
                for i, cell in enumerate(row):
                    if i < len(header_row):
                        row_data[header_row[i]] = cell
                binder_val = (row_data.get(binder_header) or "").strip().lower()
                if binder_val not in allowed_lower:
                    continue
                data.append(row_data)
            return data

    except FileNotFoundError:
        print(f"Error: File not found at {filename}")
        return None
    except Exception as e:
        print(f"An error occurred while reading the file: {e}")
        return None
    
def load_urls_from_file(filepath):
    # ... (previous implementation)
    try:
        with open(filepath, 'r', encoding='utf-8') as file:
            names = []
            for line in file:
                url = line.strip()
                if url and isinstance(url, str):
                    url = url.lower()
                    url = url.replace("/budget", "")
                    url = url.replace("/expensive", "")
                    # Only use base commander slug; ignore sub-themes (e.g. /artifacts) — we load average-deck only
                    match = re.search(r"/commanders/([^/#?]+)", url)
                    if match:
                        commander = match.group(1).strip()
                        if commander:
                            names.append((commander, None))  # no subtheme; variant is Normal/Budget/Expensive only

            return names

    except FileNotFoundError:
        print(f"Error: File not found at {filepath}")
        return None
    except Exception as e:  # Catch other potential errors
        print(f"An error occurred while reading the file: {e}")
        return None

def parse_card_list(filename):
    # ... (previous implementation)
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
