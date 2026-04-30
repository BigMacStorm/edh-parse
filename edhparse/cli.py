import requests_cache
import argparse
from copy import copy as shallow_copy
from pathlib import Path
import re
import sys
import time
import csv
import html as html_module
from urllib.parse import quote
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import requests
from utils.progress_utils import progress_bar
from core.deck import Deck, _edhrec_get_with_retry, EDHREC_SLEEP
from core.collection import Collection
from core.deck_diff import diff_decks, generate_shopping_list
from core.card import Card, _get_with_retry, SCRYFALL_SLEEP
from clients import scryfall_bulk
from .edhrec_commander_compare import inclusion_pct, run_compare_edhrec_commanders
from .edhrec_commander_themes import (
    discover_theme_slugs,
    edhrec_commander_json_url,
    extract_page_cards_and_meta,
    theme_label_from_container,
)

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
    parser.add_argument("-html", "--html", nargs="?", const="outputs/summary.html", metavar="FILE", help="Write a summary webpage. Default: outputs/summary.html")
    parser.add_argument("-pdf", "--pdf", nargs="?", const="outputs/summary.pdf", metavar="FILE", help="Export summary to PDF (uses HTML output; default: outputs/summary.pdf). Requires weasyprint.")
    parser.add_argument(
        "--latest-set-html",
        nargs="?",
        const="outputs/latest_set.html",
        metavar="FILE",
        help="Write a webpage highlighting cards from the most recent set used across all decks. Default: outputs/latest_set.html",
    )
    parser.add_argument(
        "--new-cards-html",
        nargs="?",
        const="outputs/new_cards.html",
        metavar="FILE",
        help=(
            "Write a webpage showing EDHREC-recommended cards for each commander that come from the last two release sets "
            "(based on full EDHREC card lists across Normal/Budget/Expensive, not the average deck). Default: outputs/new_cards.html"
        ),
    )
    parser.add_argument("-all", "--all", action="store_true", help="Show all cards when printing deck.")
    parser.add_argument("-fc", "--fresh_cache", action="store_true", help="To force a cache clear on lookups.")
    parser.add_argument(
        "--safe-shopping-html",
        nargs="?",
        const="outputs/safe_shopping.html",
        metavar="FILE",
        help=(
            "Write a shopping webpage for cards likely to be included based on EDHREC inclusion cutoff "
            "across average/budget/expensive commander pages. Requires --commander_list."
        ),
    )
    parser.add_argument(
        "--safe-shopping-min-inclusion-pct",
        type=float,
        default=20.0,
        metavar="PCT",
        help="With --safe-shopping-html: only include cards with inclusion >= this percent (default: 20).",
    )
    parser.add_argument(
        "--safe-shopping-min-synergy-pct",
        type=float,
        default=20.0,
        metavar="PCT",
        help="With --safe-shopping-html: only include cards with synergy >= this percent (default: 20).",
    )
    parser.add_argument(
        "--safe-shopping-stock-csv",
        metavar="FILE",
        help="With --safe-shopping-html: CSV file used to mark cards as owned from selected binders.",
    )
    parser.add_argument(
        "--safe-shopping-binders",
        nargs="+",
        metavar="BINDER",
        help="With --safe-shopping-html: binder names to count as owned (case-insensitive).",
    )
    parser.add_argument(
        "--safe-shopping-sort-default",
        choices=("inclusion", "price"),
        default="inclusion",
        help="With --safe-shopping-html: default sort mode for cards in each commander group.",
    )
    parser.add_argument(
        "--safe-shopping-min-price",
        type=float,
        default=0.0,
        metavar="USD",
        help="With --safe-shopping-html: only include cards priced at or above this amount (default: 0).",
    )
    parser.add_argument(
        "--artist-signings",
        nargs="+",
        metavar="ARG",
        help=(
            "Find cards whose artists are attending a convention. "
            "Usage: --artist-signings <stock.csv> <artists.txt> <binder1> [binder2 ...]"
        ),
    )
    parser.add_argument(
        "--compare-edhrec",
        nargs=2,
        metavar=("URL_A", "URL_B"),
        help=(
            "Compare two EDHREC commander pages (full URL or slug; optional theme path after the commander). "
            "Reports overlap score and shared cards with inclusion on each page."
        ),
    )
    parser.add_argument(
        "--compare-edhrec-min-inclusion-pct",
        type=float,
        default=None,
        metavar="PCT",
        help=(
            "With --compare-edhrec: only print shared cards whose EDHREC inclusion is strictly greater than "
            "this percent on both pages (cards without a parsed numeric inclusion are omitted)."
        ),
    )
    return parser.parse_args()

def main():
    args = build_args()
    startup_session = requests_cache.CachedSession("cache/card_cache", expire_after=3600 * 24 * 31)
    if args.fresh_cache:
        startup_session.cache.clear()
    print("Checking Scryfall bulk data...", flush=True)
    if not scryfall_bulk.ensure_loaded(startup_session):
        print(
            "Warning: could not refresh/load Scryfall bulk data. "
            "Lookups will fall back to cache/live API where needed."
        )

    # --- EDHREC commander comparison ---
    if getattr(args, "compare_edhrec", None):
        url_a, url_b = args.compare_edhrec
        try:
            run_compare_edhrec_commanders(
                startup_session,
                url_a,
                url_b,
                min_inclusion_both_pct=args.compare_edhrec_min_inclusion_pct,
            )
        except ValueError as e:
            print(f"Error: {e}")
        return

    # --- Artist signing mode ---
    if getattr(args, "artist_signings", None):
        if len(args.artist_signings) < 3:
            print(
                "Error: --artist-signings requires at least 3 arguments: "
                "<stock.csv> <artists.txt> <binder1> [binder2 ...]"
            )
            return
        stock_csv = args.artist_signings[0]
        artists_txt = args.artist_signings[1]
        binder_names = args.artist_signings[2:]
        run_artist_signings(stock_csv, binder_names, artists_txt, args)
        return

    # --- New-cards-by-set mode (EDHREC-style, full recommendations, last two sets) ---
    if getattr(args, "safe_shopping_html", None) is not None:
        if not args.commander_list:
            print("Error: --safe-shopping-html requires --commander_list pointing to your commander list file.")
            return
        if args.safe_shopping_stock_csv and not args.safe_shopping_binders:
            print("Error: --safe-shopping-stock-csv requires --safe-shopping-binders.")
            return
        run_safe_shopping_html(args)
        return

    # --- New-cards-by-set mode (EDHREC-style, full recommendations, last two sets) ---
    if getattr(args, "new_cards_html", None) is not None:
        if not args.commander_list:
            print("Error: --new-cards-html requires --commander_list pointing to your commander list file.")
            return
        run_new_cards_html(args)
        return

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
        session = requests_cache.CachedSession('cache/card_cache', expire_after=3600*24*31)
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
        session = requests_cache.CachedSession('cache/card_cache', expire_after=3600*24*31)
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
    if getattr(args, "pdf", None) is not None:
        html_path = args.html if args.html is not None else (
            args.pdf.replace(".pdf", ".html") if args.pdf.endswith(".pdf") else "outputs/summary.html"
        )
        if args.html is None:
            collection.write_summary_html(html_path, used_manabox=(manabox_data is not None))
        pdf_ok = False
        try:
            from weasyprint import HTML as WeasyHTML
            WeasyHTML(filename=html_path).write_pdf(args.pdf)
            pdf_ok = True
            print(f"Wrote PDF to {args.pdf}")
        except (ImportError, OSError, Exception) as e:
            if "libgobject" not in str(e):
                print(f"WeasyPrint failed: {e}")
            try:
                from xhtml2pdf import pisa
                with open(html_path, "r", encoding="utf-8") as html_file:
                    html_src = html_file.read()
                # xhtml2pdf doesn't support CSS custom properties; expand them to hex values
                _css_vars = {
                    "var(--bg)": "#0f0f14",
                    "var(--surface)": "#1c1e26",
                    "var(--card)": "#252830",
                    "var(--text)": "#e4e4e7",
                    "var(--muted)": "#6b7280",
                    "var(--accent)": "#7c3aed",
                    "var(--green)": "#22c55e",
                    "var(--amber)": "#f59e0b",
                    "var(--border)": "#3f3f4a",
                }
                for var_name, value in _css_vars.items():
                    html_src = html_src.replace(var_name, value)
                with open(args.pdf, "w+b") as pdf_file:
                    pisa_status = pisa.CreatePDF(html_src, dest=pdf_file, encoding="utf-8")
                if not pisa_status.err:
                    pdf_ok = True
                    print(f"Wrote PDF to {args.pdf} (via xhtml2pdf)")
                else:
                    print("xhtml2pdf reported errors while writing PDF.")
            except ImportError:
                print("PDF export requires weasyprint or xhtml2pdf. On Windows: pip install xhtml2pdf")
            except Exception as e2:
                print(f"Error writing PDF: {e2}")
    if getattr(args, "latest_set_html", None) is not None:
        collection.write_latest_set_html(args.latest_set_html)

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


def parse_artist_names_file(filepath: Path):
    """Parse a simple text file of artist names (one name per line)."""
    names = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            name = line.strip()
            if not name:
                continue
            names.append(name)
    return names


def load_stock_rows_for_binders(filename, binder_names):
    """
    Load stock CSV rows and keep only rows where binder matches any provided binder name.
    Binder matching is case-insensitive.
    """
    try:
        with open(filename, "r", encoding="utf-8") as csvfile:
            reader = csv.reader(csvfile, delimiter=",", quotechar='"')
            header_row = next(reader)
            if not header_row:
                return []

            name_col = _find_csv_col(header_row, "Name", "Card")
            qty_col = _find_csv_col(header_row, "Quantity", "Qty", "Qty.")
            binder_col = _find_csv_col(header_row, "Binder Name", "Binder", "Folder")
            set_col = _find_csv_col(header_row, "Set code", "Set", "Set Code")
            collector_col = _find_csv_col(header_row, "Collector number", "Collector Number", "Number")
            scryfall_id_col = _find_csv_col(header_row, "Scryfall ID", "Scryfall Id")
            if name_col is None or binder_col is None:
                print("Error: CSV must contain card and binder columns.")
                return None

            binder_wants = {(b or "").strip().lower() for b in binder_names if (b or "").strip()}
            if not binder_wants:
                print("Error: No binder names were provided.")
                return None

            binder_header = header_row[binder_col] if binder_col < len(header_row) else ""
            out_rows = []
            for row in reader:
                row_data = {}
                for i, cell in enumerate(row):
                    if i < len(header_row):
                        row_data[header_row[i]] = cell
                binder_val = (row_data.get(binder_header) or "").strip()
                if binder_val.lower() not in binder_wants:
                    continue
                card_name = (row_data.get(header_row[name_col]) or "").strip()
                if not card_name:
                    continue
                qty = 1
                if qty_col is not None and qty_col < len(row):
                    raw_qty = (row[qty_col] or "").strip()
                    if raw_qty.isdigit():
                        qty = int(raw_qty)
                out_rows.append(
                    {
                        "name": card_name,
                        "binder": binder_val,
                        "quantity": qty,
                        "set_code": (row[set_col].strip() if set_col is not None and set_col < len(row) else ""),
                        "collector_number": (
                            row[collector_col].strip() if collector_col is not None and collector_col < len(row) else ""
                        ),
                        "scryfall_id": (
                            row[scryfall_id_col].strip() if scryfall_id_col is not None and scryfall_id_col < len(row) else ""
                        ),
                    }
                )
            return out_rows
    except FileNotFoundError:
        print(f"Error: File not found at {filename}")
        return None
    except Exception as e:
        print(f"An error occurred while reading the file: {e}")
        return None


def run_artist_signings(stock_csv, binder_names, artists_txt, args):
    """
    Find cards in selected binders whose Scryfall artist matches an artist in the provided text file.
    Output is grouped by artist, with cards sorted by value (highest first).
    """
    stock_path = Path(stock_csv)
    if not stock_path.is_file():
        print(f"Error: Stock CSV file not found: {stock_csv}")
        return
    artist_path = Path(artists_txt)
    if not artist_path.is_file():
        print(f"Error: Artist list file not found: {artists_txt}")
        return

    artist_names = parse_artist_names_file(artist_path)
    if not artist_names:
        print("No artist names found in the artist list file.")
        return
    artist_lookup = {(name or "").strip().lower(): name for name in artist_names}

    stock_rows = load_stock_rows_for_binders(stock_csv, binder_names)
    if stock_rows is None:
        return
    if not stock_rows:
        print("No matching cards found in the selected binders.")
        return

    # Aggregate duplicates by exact printing + binder before lookups.
    card_binder_qty = Counter()
    for row in stock_rows:
        key = (
            row["name"],
            row.get("set_code", ""),
            row.get("collector_number", ""),
            row.get("scryfall_id", ""),
            row["binder"],
        )
        card_binder_qty[key] += int(row.get("quantity", 1) or 1)

    session = requests_cache.CachedSession("cache/card_cache", expire_after=3600 * 24 * 31)
    if args.fresh_cache:
        session.cache.clear()

    def _image_from_scryfall_data(data):
        """Return a reasonable image URL for this printing."""
        if not isinstance(data, dict):
            return None
        image_uris = data.get("image_uris") or {}
        if image_uris.get("large"):
            return image_uris.get("large")
        if image_uris.get("normal"):
            return image_uris.get("normal")
        if image_uris.get("png"):
            return image_uris.get("png")
        if image_uris.get("small"):
            return image_uris.get("small")
        faces = data.get("card_faces") or []
        for face in faces:
            if not isinstance(face, dict):
                continue
            face_images = face.get("image_uris") or {}
            if face_images.get("large"):
                return face_images.get("large")
            if face_images.get("normal"):
                return face_images.get("normal")
            if face_images.get("png"):
                return face_images.get("png")
            if face_images.get("small"):
                return face_images.get("small")
        return None

    def lookup_printing(row_name, row_set, row_collector, row_scryfall_id):
        """Lookup artist/price/image for an exact printing, with graceful fallback."""
        # 1) Best: exact scryfall UUID from stock export
        if row_scryfall_id:
            data = scryfall_bulk.get_card_by_id(row_scryfall_id)
            if data is not None:
                return data.get("artist"), Card(session, args).get_price(data), _image_from_scryfall_data(data)
            url = f"https://api.scryfall.com/cards/{row_scryfall_id}"
            response, err = _get_with_retry(session, url, params=None, timeout=8, max_retries=5)
            if not err and response is not None:
                d = response.json()
                return d.get("artist"), Card(session, args).get_price(d), _image_from_scryfall_data(d)

        # 2) Exact printing route by set code + collector number
        if row_set and row_collector:
            url = f"https://api.scryfall.com/cards/{row_set.lower()}/{row_collector}"
            response, err = _get_with_retry(session, url, params=None, timeout=8, max_retries=5)
            if not err and response is not None:
                d = response.json()
                return d.get("artist"), Card(session, args).get_price(d), _image_from_scryfall_data(d)

        # 3) Fallback: name-level lookup (less precise)
        card = Card(session, args)
        card.search_card(row_name)
        if card.error:
            return None, 0.0, None
        return card.artist, float(card.price or 0.0), card.card_pic

    row_to_artist_and_price = {}
    lookup_rows = sorted(card_binder_qty.keys(), key=lambda t: (t[0].lower(), t[1].lower(), t[2]))
    with progress_bar(len(lookup_rows), initial_value=0) as bar:
        for i, row_key in enumerate(lookup_rows):
            card_name, set_code, collector_number, scryfall_id, _binder = row_key
            bar.update(i, info=f"Scryfall lookup for {card_name}")
            row_to_artist_and_price[row_key] = lookup_printing(
                card_name,
                set_code,
                collector_number,
                scryfall_id,
            )

    grouped = defaultdict(list)
    for row_key, qty in card_binder_qty.items():
        card_name, set_code, collector_number, _scryfall_id, binder_name = row_key
        artist, unit_price, card_art = row_to_artist_and_price.get(row_key, (None, 0.0, None))
        if not artist:
            continue
        artist_key = artist.lower()
        if artist_key not in artist_lookup:
            continue
        total_value = unit_price * qty
        grouped[artist_lookup[artist_key]].append(
            {
                "card": card_name,
                "binder": binder_name,
                "qty": qty,
                "unit_price": unit_price,
                "total_value": total_value,
                "set_code": set_code,
                "collector_number": collector_number,
                "card_art": card_art,
            }
        )

    if not grouped:
        print("No cards matched the provided artist list.")
        return

    out_path = "outputs/artist_signings.html"
    write_artist_signings_html(out_path, grouped, binder_names)
    print(f"Wrote artist signings report to {out_path}")


def write_artist_signings_html(out_path, grouped, binder_names):
    """Render a pretty HTML report grouped by artist and sorted by value."""
    artist_totals = {}
    global_total = 0.0
    global_count = 0

    # Normalize + sort each artist's cards by value descending.
    normalized = {}
    for artist, cards in grouped.items():
        sorted_cards = sorted(cards, key=lambda c: c["total_value"], reverse=True)
        normalized[artist] = sorted_cards
        total = sum(c["total_value"] for c in sorted_cards)
        artist_totals[artist] = total
        global_total += total
        global_count += len(sorted_cards)

    artists_sorted = sorted(
        normalized.keys(),
        key=lambda a: (artist_totals.get(a, 0.0), a.lower()),
        reverse=True,
    )
    binder_text = ", ".join(binder_names)

    html_parts = [
        """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Artist Signings Report</title>
<style>
:root {
  --bg: #0f1115;
  --surface: #171b22;
  --card: #1f2530;
  --text: #e7edf6;
  --muted: #9eabc2;
  --accent: #6aa9ff;
  --green: #3ddc97;
  --border: #2b3443;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: "Segoe UI", system-ui, sans-serif;
  line-height: 1.45;
  padding: 1.25rem;
}
.container { max-width: 1200px; margin: 0 auto; }
h1 { margin: 0; font-size: 1.8rem; }
.sub { color: var(--muted); margin-top: 0.4rem; }
.meta {
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
  margin-top: 0.9rem;
}
.pill {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.35rem 0.75rem;
  color: var(--muted);
  font-size: 0.86rem;
}
.artist {
  margin-top: 1.1rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}
.artist-head {
  padding: 0.7rem 0.9rem;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}
.artist-name { font-size: 1.05rem; font-weight: 600; }
.artist-total { color: var(--green); font-weight: 600; }
table { width: 100%; border-collapse: collapse; }
th, td {
  text-align: left;
  padding: 0.55rem 0.7rem;
  border-bottom: 1px solid var(--border);
  font-size: 0.9rem;
}
.art-col { width: 352px; }
.art-thumb {
  width: 336px;
  height: auto;
  border-radius: 6px;
  border: 1px solid var(--border);
  display: block;
}
th {
  color: var(--muted);
  font-weight: 600;
  background: var(--card);
}
tr:hover td { background: rgba(255,255,255,0.02); }
.right { text-align: right; }
.money { color: var(--green); font-variant-numeric: tabular-nums; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
@media print {
  body { background: #fff; color: #111; }
  .artist, .pill { border-color: #ccc; }
  th { background: #f1f1f1; color: #222; }
}
</style>
</head>
<body>
<div class="container">
  <h1>Artist Signings Report</h1>
""",
        f'  <div class="sub">Cards in selected binders with artists from your convention list.</div>\n',
        '  <div class="meta">\n',
        f'    <div class="pill">Binders: {html_module.escape(binder_text)}</div>\n',
        f'    <div class="pill">Matched artists: {len(artists_sorted)}</div>\n',
        f'    <div class="pill">Matched cards: {global_count}</div>\n',
        f'    <div class="pill">Total value: ${global_total:.2f}</div>\n',
        "  </div>\n",
    ]

    for artist in artists_sorted:
        cards = normalized[artist]
        html_parts.append('<section class="artist">\n')
        html_parts.append('  <div class="artist-head">\n')
        html_parts.append(f'    <div class="artist-name">{html_module.escape(artist)}</div>\n')
        html_parts.append(f'    <div class="artist-total">${artist_totals[artist]:.2f}</div>\n')
        html_parts.append("  </div>\n")
        html_parts.append("  <table>\n")
        html_parts.append(
            "    <thead><tr>"
            "<th class='art-col'>Art</th><th>Card</th><th>Binder</th><th class='right'>Qty</th>"
            "<th class='right'>Cost</th><th class='right'>Value</th>"
            "</tr></thead>\n"
        )
        html_parts.append("    <tbody>\n")
        for entry in cards:
            if entry.get("set_code") and entry.get("collector_number"):
                scryfall_url = (
                    f"https://scryfall.com/card/"
                    f"{quote(entry['set_code'].lower(), safe='')}/"
                    f"{quote(str(entry['collector_number']), safe='')}/"
                    f"{quote(entry['card'].lower().replace(' ', '-'), safe='-')}"
                )
            else:
                scryfall_query = quote(
                    f'!"{entry["card"]}" artist:"{artist}" unique:prints',
                    safe="",
                )
                scryfall_url = f"https://scryfall.com/search?q={scryfall_query}"
            html_parts.append(
                "      <tr>"
                f"<td>{('<img class=\"art-thumb\" src=\"' + html_module.escape(entry['card_art']) + '\" alt=\"' + html_module.escape(entry['card']) + '\">') if entry.get('card_art') else ''}</td>"
                f"<td><a href=\"{html_module.escape(scryfall_url)}\" target=\"_blank\" rel=\"noopener\">"
                f"{html_module.escape(entry['card'])}</a></td>"
                f"<td>{html_module.escape(entry['binder'])}</td>"
                f"<td class='right'>{entry['qty']}</td>"
                f"<td class='right money'>${entry['unit_price']:.2f}</td>"
                f"<td class='right money'>${entry['total_value']:.2f}</td>"
                "</tr>\n"
            )
        html_parts.append("    </tbody>\n")
        html_parts.append("  </table>\n")
        html_parts.append("</section>\n")

    html_parts.append("</div>\n</body>\n</html>")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(html_parts))

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
    with open(filepath, 'r', encoding='utf-8') as file:
        names = []
        for line in file:
            raw = line.strip()
            if not raw or not isinstance(raw, str):
                continue
            # If the line looks like a URL with /commanders/, use the existing URL-based parsing.
            lower = raw.lower()
            lower = lower.replace("/budget", "")
            lower = lower.replace("/expensive", "")
            match = re.search(r"/commanders/([^/#?]+)", lower)
            if match:
                commander = match.group(1).strip()
                if commander:
                    names.append((commander, None))  # no subtheme; variant is Normal/Budget/Expensive only
                continue

            # Otherwise, treat the line as a plain commander name (or partner pair) and convert to EDHREC slug.
            # For partners, use "Name One // Name Two" and join their individual slugs with a single dash.
            if "//" in raw:
                left, right = raw.split("//", 1)
                left = left.strip()
                right = right.strip()
                if left and right:
                    left_slug = get_edhrec_name(left)
                    right_slug = get_edhrec_name(right)
                    combined = f"{left_slug}-{right_slug}"
                    names.append((combined, None))
                continue

            slug = get_edhrec_name(raw)
            if slug:
                names.append((slug, None))

        return names


def _get_recent_sets(session, count=2):
    """Return a list of the last `count` real sets from Scryfall (expansion/core/commander/draft_innovation)."""
    allowed_set_types = {"expansion", "core", "commander", "draft_innovation"}
    sets_url = "https://api.scryfall.com/sets"
    params = {"order": "released", "dir": "desc"}
    response, err = _get_with_retry(session, sets_url, params=params, timeout=10)
    if err:
        print(f"Error fetching Scryfall sets: {err}")
        return []
    if not getattr(response, "from_cache", True):
        time.sleep(SCRYFALL_SLEEP)
    data = response.json()
    recent = []
    for s in data.get("data", []):
        if s.get("set_type") in allowed_set_types:
            recent.append(
                {
                    "code": s.get("code"),
                    "name": s.get("name"),
                    "released_at": s.get("released_at"),
                }
            )
            if len(recent) >= count:
                break
    return recent


def _load_owned_names_from_stock_csv(filename, binder_names):
    """Return a set of owned card names (lowercased) within selected binders."""
    try:
        with open(filename, "r", encoding="utf-8") as csvfile:
            reader = csv.reader(csvfile, delimiter=",", quotechar='"')
            header_row = next(reader)
            if not header_row:
                return set()

            name_col = _find_csv_col(header_row, "Name", "Card")
            binder_col = _find_csv_col(header_row, "Binder Name", "Binder", "Folder")
            if name_col is None or binder_col is None:
                print("Error: safe-shopping CSV must include card and binder columns.")
                return None

            wanted = {(b or "").strip().lower() for b in (binder_names or []) if (b or "").strip()}
            if not wanted:
                return set()

            owned_names = set()
            for row in reader:
                if len(row) <= max(name_col, binder_col):
                    continue
                binder = (row[binder_col] or "").strip().lower()
                if binder not in wanted:
                    continue
                name = (row[name_col] or "").strip()
                if name:
                    owned_names.add(name.lower())
            return owned_names
    except FileNotFoundError:
        print(f"Error: File not found at {filename}")
        return None
    except Exception as e:
        print(f"An error occurred while reading the file: {e}")
        return None


def run_safe_shopping_html(args):
    """
    Build a commander-grouped shopping page from average/budget/expensive EDHREC lists.
    Includes cards at or above inclusion cutoff and marks ownership from selected binders.
    """
    names_and_types = load_urls_from_file(args.commander_list)
    if not names_and_types:
        print("No commanders found in commander_list.")
        return

    inclusion_cutoff = float(args.safe_shopping_min_inclusion_pct or 0.0)
    synergy_cutoff = float(args.safe_shopping_min_synergy_pct or 0.0)
    min_price = float(args.safe_shopping_min_price or 0.0)
    owned_names = set()
    if args.safe_shopping_stock_csv:
        owned_names = _load_owned_names_from_stock_csv(
            args.safe_shopping_stock_csv,
            args.safe_shopping_binders or [],
        )
        if owned_names is None:
            return

    session = requests_cache.CachedSession("cache/card_cache", expire_after=3600 * 24 * 31)
    if args.fresh_cache:
        session.cache.clear()

    card_cache = {}  # name_lower -> card metadata
    output_rows = []
    tier_suffixes = [("", "average")]

    with progress_bar(len(names_and_types), initial_value=0) as commander_bar:
        idx = 0
        for commander_slug, _variant in names_and_types:
            commander_bar.update(idx, info=f"Safe shopping lookup for {commander_slug.replace('-', ' ')}")
            idx += 1

            merged = {}  # card_name -> row meta (max inclusion across tiers)
            commander_title = commander_slug.replace("-", " ").title()

            for suffix, tier_label in tier_suffixes:
                url = f"https://json.edhrec.com/pages/commanders/{commander_slug}{suffix}.json"
                response, err = _edhrec_get_with_retry(session, url, timeout=10)
                if err:
                    continue
                if not getattr(response, "from_cache", True):
                    time.sleep(EDHREC_SLEEP)
                try:
                    data = response.json()
                except Exception:
                    continue

                container = data.get("container") or {}
                json_dict = container.get("json_dict") or {}
                cardlists = json_dict.get("cardlists") or []
                header = container.get("header") or json_dict.get("header")
                if header:
                    commander_title = header.replace(" (Commander)", "").strip()

                for section in cardlists:
                    for cv in section.get("cardviews") or []:
                        name = (cv.get("name") or "").strip()
                        if not name:
                            continue
                        inc_pct = inclusion_pct(cv)
                        syn_raw = cv.get("synergy")
                        syn_pct = None
                        try:
                            if syn_raw is not None:
                                syn_pct = float(syn_raw)
                                if -2.0 <= syn_pct <= 2.0:
                                    syn_pct *= 100.0
                        except (TypeError, ValueError):
                            syn_pct = None

                        if inc_pct is None or inc_pct < inclusion_cutoff:
                            continue
                        if syn_pct is None or syn_pct < synergy_cutoff:
                            continue
                        cur = merged.get(name)
                        if cur is None or inc_pct > cur["inclusion_pct"]:
                            merged[name] = {
                                "name": name,
                                "inclusion_pct": inc_pct,
                                "inclusion_label": f"{inc_pct:.1f}%",
                                "synergy_pct": syn_pct,
                                "synergy_label": f"{syn_pct:.1f}%",
                                "tiers": {tier_label},
                            }
                        else:
                            cur["tiers"].add(tier_label)

            if not merged:
                output_rows.append(
                    {
                        "slug": commander_slug,
                        "name": commander_title,
                        "cards": [],
                        "owned_pct": 0.0,
                        "owned_count": 0,
                        "card_count": 0,
                    }
                )
                continue

            cards = []
            for name in sorted(merged.keys(), key=str.lower):
                row = merged[name]
                key = name.lower()
                cached = card_cache.get(key)
                if cached is None:
                    c = Card(session, args, is_commander=False)
                    c.search_card(name)
                    if c.error:
                        cached = {
                            "price": None,
                            "type_line": "",
                            "set_name": "",
                            "set_code": "",
                            "card_pic": "",
                        }
                    else:
                        cached = {
                            "price": c.price,
                            "type_line": c.type_line or "",
                            "set_name": c.set_name or "",
                            "set_code": c.set_code or "",
                            "card_pic": c.card_pic or "",
                        }
                    card_cache[key] = cached

                type_line = (cached.get("type_line") or "").lower()
                if "land" in type_line:
                    continue

                price_val = cached.get("price")
                price_num = float(price_val) if isinstance(price_val, (int, float)) and price_val is not None else -1.0
                if price_num < min_price:
                    continue

                owned = key in owned_names if owned_names else False
                cards.append(
                    {
                        "name": name,
                        "inclusion_pct": row["inclusion_pct"],
                        "inclusion_label": row["inclusion_label"],
                        "synergy_pct": row["synergy_pct"],
                        "synergy_label": row["synergy_label"],
                        "tiers": sorted(list(row["tiers"])),
                        "owned": owned,
                        "price": cached["price"],
                        "type_line": cached["type_line"],
                        "set_name": cached["set_name"],
                        "set_code": cached["set_code"],
                        "card_pic": cached["card_pic"],
                    }
                )

            owned_count = sum(1 for c in cards if c["owned"])
            card_count = len(cards)
            owned_pct = (100.0 * owned_count / card_count) if card_count else 0.0
            output_rows.append(
                {
                    "slug": commander_slug,
                    "name": commander_title,
                    "cards": cards,
                    "owned_pct": owned_pct,
                    "owned_count": owned_count,
                    "card_count": card_count,
                }
            )

    out_path = args.safe_shopping_html or "outputs/safe_shopping.html"
    from renderers.safe_shopping_renderer import write_safe_shopping_html as _write_safe_shopping_html

    _write_safe_shopping_html(
        out_path=out_path,
        rows=output_rows,
        inclusion_cutoff=inclusion_cutoff,
        synergy_cutoff=synergy_cutoff,
        min_price=min_price,
        default_sort=args.safe_shopping_sort_default or "inclusion",
        has_ownership_data=bool(args.safe_shopping_stock_csv),
        stock_csv=args.safe_shopping_stock_csv or "",
        binder_names=args.safe_shopping_binders or [],
    )


def _run_new_cards_html_impl(args):
    """Standalone flow: for each commander, pull full EDHREC recs (Normal/Budget/Expensive),
    then show cards from the last two global release sets."""
    names_and_types = load_urls_from_file(args.commander_list)
    if not names_and_types:
        print("No commanders found in commander_list.")
        return

    session = requests_cache.CachedSession("cache/card_cache", expire_after=3600 * 24 * 31)
    if args.fresh_cache:
        session.cache.clear()

    output_per_commander = []

    def _fmt_pct(val):
        """Heuristic formatter for EDHREC-style percentages."""
        if val is None:
            return None
        try:
            v = float(val)
        except (TypeError, ValueError):
            return None
        # EDHREC JSON often stores fractions like 0.042 -> 4.2%.
        if -2.0 <= v <= 2.0:
            v *= 100.0
        return f"{v:.1f}%"

    # Outer progress bar over commanders
    total_commanders = len(names_and_types)
    with progress_bar(total_commanders, initial_value=0) as commander_bar:
        idx = 0
        for commander_slug, _variant in names_and_types:
            info_name = commander_slug.replace("-", " ")
            commander_bar.update(idx, info=f"Loading EDHREC data for {info_name}")

            # EDHREC: 3 price tiers × (Average hub + up to 5 archetype themes). Stats always
            # come from the JSON for the selected tier+theme URL (cached per commander).
            theme_path_slugs = discover_theme_slugs(session, commander_slug)
            theme_options = [("", "Average")]
            page_cache = {}

            def _fetch_cmd_json(theme_slug: str, tier_slug: str):
                key = (theme_slug or "", tier_slug or "")
                if key in page_cache:
                    return page_cache[key]
                url = edhrec_commander_json_url(
                    commander_slug,
                    theme_slug=theme_slug or "",
                    tier_slug=tier_slug or "",
                )
                response, err = _edhrec_get_with_retry(session, url, timeout=10)
                if err:
                    page_cache[key] = None
                    return None
                if not getattr(response, "from_cache", True):
                    time.sleep(EDHREC_SLEEP)
                try:
                    data = response.json()
                except Exception:
                    page_cache[key] = None
                    return None
                page_cache[key] = data
                return data

            commander_title = None
            main_hub = _fetch_cmd_json("", "")
            if main_hub:
                container = main_hub.get("container") or {}
                json_dict = container.get("json_dict") or {}
                header = container.get("header") or json_dict.get("header")
                if header:
                    commander_title = header.replace(" (Commander)", "").strip()

            for th in theme_path_slugs:
                th_data = _fetch_cmd_json(th, "")
                if not th_data:
                    continue
                container = th_data.get("container") or {}
                theme_options.append((th, theme_label_from_container(container, th)))

            tier_order = [("", "average"), ("budget", "budget"), ("expensive", "expensive")]

            all_names = set()
            pane_orders_meta = {}
            for tier_path, tier_key in tier_order:
                for theme_slug, _tlabel in theme_options:
                    pane_key = f"{tier_key}::{theme_slug}"
                    data = _fetch_cmd_json(theme_slug, tier_path)
                    if not data:
                        pane_orders_meta[pane_key] = ([], {})
                        continue
                    ordered_n, meta = extract_page_cards_and_meta(data, fmt_synergy=_fmt_pct)
                    pane_orders_meta[pane_key] = (ordered_n, meta)
                    all_names.update(ordered_n)

            if not all_names:
                idx += 1
                continue

            cards_by_name = {}
            name_list = sorted(all_names)
            with progress_bar(len(name_list), initial_value=0) as card_bar:
                for cidx, name in enumerate(name_list):
                    card_bar.update(cidx, info=f"Scryfall lookup for {name}")
                    card = Card(session, args, is_commander=False)
                    card.search_card(name)
                    if card.error or not card.set_code:
                        continue
                    cards_by_name[name] = card

            if not cards_by_name:
                idx += 1
                continue

            panes = {}
            for tier_path, tier_key in tier_order:
                for theme_slug, _ in theme_options:
                    pane_key = f"{tier_key}::{theme_slug}"
                    ordered_n, meta = pane_orders_meta.get(pane_key) or ([], {})
                    plist = []
                    for name in ordered_n:
                        base = cards_by_name.get(name)
                        if not base:
                            continue
                        c = shallow_copy(base)
                        m = meta.get(name) or {}
                        c.edh_inclusion = m.get("inclusion")
                        c.edh_synergy = m.get("synergy")
                        plist.append(c)
                    panes[pane_key] = plist

            commander_card = Card(session, args, is_commander=True)
            display_name = commander_title or commander_slug.replace("-", " ").title()
            commander_card.search_card(display_name)
            if commander_card.error:
                commander_card = None

            output_per_commander.append(
                {
                    "slug": commander_slug,
                    "name": display_name,
                    "commander_card": commander_card,
                    "tier_options": [
                        {"id": "average", "label": "Average"},
                        {"id": "budget", "label": "Budget"},
                        {"id": "expensive", "label": "Expensive"},
                    ],
                    "theme_options": [
                        {"slug": tslug, "label": tlab} for tslug, tlab in theme_options
                    ],
                    "panes": panes,
                }
            )

            idx += 1

    if not output_per_commander:
        print("No recommended cards found for any commander.")
        return

    # Build an eligible set list from Scryfall for the last ~6 months.
    # We intentionally use Scryfall's sets listing so the filter isn't limited to
    # only the sets that happen to appear in EDHREC's current recommendations.
    cutoff_date = (datetime.utcnow().date() - timedelta(days=183)).isoformat()
    sets_url = "https://api.scryfall.com/sets"
    params = {"order": "released", "dir": "desc"}
    response, err = _get_with_retry(session, sets_url, params=params, timeout=10)
    if err:
        print(f"Error fetching Scryfall sets: {err}")
        return
    if not getattr(response, "from_cache", True):
        time.sleep(SCRYFALL_SLEEP)
    sets_data = response.json()

    # Map set_code -> metadata, restricted to last 6 months.
    eligible_sets_info = {}
    for s in sets_data.get("data", []):
        code = (s.get("code") or "").strip()
        released_at = s.get("released_at") or ""
        if not code or not released_at:
            continue
        # Secret Lair drops use Scryfall's `sld` set code; include it even if the
        # set's own release date isn't within our window so we can still find
        # recommended Secret Lair cards.
        if released_at < cutoff_date and code.lower() != "sld":
            continue
        eligible_sets_info[code.lower()] = {
            "code": code.lower(),
            "name": s.get("name") or code.upper(),
            "set_type": s.get("set_type"),
            "released_at": released_at,
        }

    if not eligible_sets_info:
        print("No Scryfall sets found in the last ~6 months.")
        return

    # Count how many EDHREC-recommended (non-reprint) cards we actually have per set,
    # but only within the eligible Scryfall set universe.
    set_card_counts = Counter()
    for entry in output_per_commander:
        for card_list in entry["panes"].values():
            for card in card_list:
                scode = (getattr(card, "set_code", None) or "").lower()
                if not scode:
                    continue
                if scode not in eligible_sets_info:
                    continue
                if getattr(card, "is_reprint", False):
                    continue
                set_card_counts[scode] += 1

    sets_to_consider = {code for code, cnt in set_card_counts.items() if cnt > 0}
    if not sets_to_consider:
        # Still render the page so commanders with zero in-window cards stay visible.
        if not output_per_commander:
            print("No non-reprint EDHREC recommended cards found in the last ~6 months of sets.")
            return
        sets_to_consider = set(eligible_sets_info.keys())

    # Filter each commander’s cards down to just those eligible sets, and skip Scryfall
    # printings that are marked as reprints so staples/basic lands don't flood the list.
    filtered_output = []
    for entry in output_per_commander:
        panes_f = {}
        for pk, card_list in entry["panes"].items():
            flist = []
            for c in card_list:
                scode = (getattr(c, "set_code", None) or "").lower()
                if not scode or scode not in sets_to_consider:
                    continue
                if getattr(c, "is_reprint", False):
                    continue
                flist.append(c)
            panes_f[pk] = flist
        entry = dict(entry)
        entry["panes"] = panes_f
        filtered_output.append(entry)

    # Build HTML and render a set filter UI. Default selection: sets with >=2 matches.
    set_codes_sorted = sorted(
        list(sets_to_consider),
        key=lambda code: eligible_sets_info[code]["released_at"],
        reverse=True,
    )
    label_parts = []
    for code in set_codes_sorted:
        label_parts.append(f"{eligible_sets_info[code]['name']} ({code.upper()})")
        if len(label_parts) >= 4:
            break
    set_label = " / ".join(label_parts)

    # Render the HTML page in a dedicated renderer module.
    # (We keep the older inline HTML assembly below as legacy/unreachable code.)
    out_path = args.new_cards_html or "outputs/new_cards.html"
    from renderers.new_cards_renderer import write_new_cards_html as _write_new_cards_html

    _write_new_cards_html(
        out_path=out_path,
        filtered_output=filtered_output,
        eligible_sets_info=eligible_sets_info,
        set_card_counts=set_card_counts,
        set_codes_sorted=set_codes_sorted,
        set_label=set_label,
    )
    return

    html_parts = [
        """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Recent-Set Cards by Commander</title>
<style>
:root { --bg: #0f0f14; --surface: #1c1e26; --card: #252830; --text: #e4e4e7; --muted: #6b7280; --accent: #7c3aed; --green: #22c55e; --amber: #f59e0b; }
* { box-sizing: border-box; }
body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 1.5rem 2rem; line-height: 1.5; }
h1 { font-size: 1.75rem; margin-bottom: 0.25rem; color: var(--text); font-weight: 600; }
.subtitle { font-size: 0.9rem; color: var(--muted); margin-bottom: 1.5rem; }
.commander-section { background: var(--surface); border-radius: 12px; padding: 1.25rem; margin-bottom: 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.3); }
.commander-header { display: flex; align-items: flex-start; gap: 1rem; flex-wrap: wrap; margin-bottom: 1rem; }
.commander-pic { width: 200px; height: 280px; object-fit: contain; border-radius: 8px; border: 1px solid var(--card); }
.commander-meta { flex: 1; min-width: 180px; }
.commander-meta h2 { margin: 0 0 0.4rem; font-size: 1.2rem; }
.commander-meta p { margin: 0.1rem 0; font-size: 0.85rem; color: var(--muted); }
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.75rem; margin-top: 0.5rem; }
.card-tile { background: var(--card); border-radius: 10px; padding: 0.6rem; display: flex; flex-direction: column; gap: 0.3rem; }
.card-title { font-size: 0.9rem; font-weight: 600; }
.card-meta { font-size: 0.75rem; color: var(--muted); }
.card-price { font-size: 0.8rem; margin-top: 0.2rem; color: var(--green); }
.card-image { width: 100%; border-radius: 6px; border: 1px solid #111; object-fit: contain; margin-top: 0.25rem; }
.card-oracle { font-size: 0.75rem; color: var(--muted); margin-top: 0.35rem; line-height: 1.3; }
.card-scryfall-link { font-size: 0.8rem; color: var(--accent); margin-top: 0.25rem; display: inline-block; }
.badge { display: inline-block; padding: 0.1rem 0.4rem; border-radius: 999px; font-size: 0.7rem; background: rgba(124,58,237,0.15); color: var(--accent); margin-left: 0.35rem; }
.set-filter-wrap { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 0.75rem 1rem; margin: 0.5rem 0 1rem; }
.set-filter-title { font-size: 0.95rem; font-weight: 600; color: var(--muted); margin-bottom: 0.5rem; }
.set-checkboxes { display: flex; flex-wrap: wrap; gap: 0.6rem 0.9rem; }
.set-option { display: inline-flex; align-items: center; gap: 0.35rem; font-size: 0.85rem; color: var(--text); background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 999px; padding: 0.25rem 0.6rem; }
.set-option input { accent-color: var(--accent); }
.set-pill-meta { font-size: 0.8rem; color: var(--muted); }
.card-tile.filtered-out { display: none !important; }
.commander-section.filtered-out { display: none !important; }
@media print {
  body { background: #fff; color: #111; padding: 0.5rem; }
  :root { --bg: #fff; --surface: #f5f5f5; --card: #eee; --text: #111; --muted: #444; }
  .set-filter-wrap { display: none !important; }
}
</style>
</head>
<body>
<h1>Recent-Set Cards by Commander</h1>
<p class="subtitle">Showing EDHREC-recommended cards from sets released in the last ~6 months (filtered by checkbox): <strong>""" + set_label + """</strong>.</p>
<div>
"""
    ]

    # Set filters UI (checkboxes). Default selected: sets with >=2 matching cards.
    checkbox_parts = []
    for code in set_codes_sorted:
        meta = eligible_sets_info.get(code) or {}
        set_name = meta.get("name") or code.upper()
        cnt = set_card_counts.get(code, 0)
        checked_attr = "checked" if cnt >= 2 else ""
        released_at = meta.get("released_at", "")
        checkbox_parts.append(
            f'<label class="set-option" title="{html_module.escape(released_at)}">'
            f'<input type="checkbox" class="set-cb" value="{html_module.escape(code)}" {checked_attr}> '
            f'{html_module.escape(set_name)} ({code.upper()}) '
            f'<span class="set-pill-meta">{cnt}</span></label>\n'
        )

    html_parts.append(
        '<div class="set-filter-wrap">\n'
        '<div class="set-filter-title">Sets (last ~6 months + Secret Lair “sld”)</div>\n'
        '<div class="set-checkboxes">\n'
        + "".join(checkbox_parts) +
        '</div>\n'
        '<div class="set-pill-meta" style="margin-top:0.5rem;">Tip: uncheck sets to hide cards instantly.</div>\n'
        '</div>\n'
    )

    for entry in filtered_output:
        name = entry["name"]
        commander_card = entry["commander_card"]
        cards = entry["cards"]
        html_parts.append('<section class="commander-section">\n')
        html_parts.append('<div class="commander-header">\n')
        if commander_card and commander_card.card_pic:
            pic = (commander_card.card_pic or "").replace("/normal/", "/large/").replace("/small/", "/large/")
            html_parts.append(f'<img class="commander-pic" src="{pic}" alt="{name}" loading="lazy">\n')
        html_parts.append('<div class="commander-meta">\n')
        html_parts.append(f'<h2>{name}</h2>\n')
        if commander_card and commander_card.type_line:
            html_parts.append(f'<p>{commander_card.type_line.replace("Legendary Creature — ", "")}</p>\n')
        if commander_card and commander_card.color_identity:
            html_parts.append(f'<p>Color identity: {commander_card.color_identity}</p>\n')
        html_parts.append(f'<p>Cards from recent sets: {len(cards)}</p>\n')
        html_parts.append('</div>\n</div>\n')
        html_parts.append('<div class="card-grid">\n')
        for card in cards:
            scode = (getattr(card, "set_code", None) or "").lower()
            html_parts.append(f'<article class="card-tile" data-set-code="{html_module.escape(scode)}">\n')
            html_parts.append(f'<div class="card-title">{card.name or ""}</div>\n')
            meta_bits = []
            if card.type_line:
                meta_bits.append(card.type_line)
            if card.rarity:
                meta_bits.append(card.rarity.title())
            if card.set_name and card.set_code:
                meta_bits.append(f"{card.set_name} ({card.set_code.upper()})")
            if meta_bits:
                joined = " · ".join(meta_bits)
                html_parts.append(f'<div class="card-meta">{joined}</div>\n')
            # EDHREC stats: inclusion and synergy if available
            stats_bits = []
            if getattr(card, "edh_inclusion", None):
                stats_bits.append(f"Inclusion rate: {card.edh_inclusion}")
            if getattr(card, "edh_synergy", None):
                stats_bits.append(f"Synergy: {card.edh_synergy}")
            if stats_bits:
                html_parts.append(f'<div class="card-meta">{" · ".join(stats_bits)}</div>\n')
            if card.price is not None:
                html_parts.append(f'<div class="card-price">${card.price:.2f}</div>\n')
            if card.card_pic:
                html_parts.append(f'<img class="card-image" src="{card.card_pic}" alt="{card.name or ""}" loading="lazy">\n')
            if card.oracle_text:
                text_esc = html_module.escape(card.oracle_text).replace("\n", "<br>")
                html_parts.append(f'<div class="card-oracle">{text_esc}</div>\n')
            scryfall_url = "https://scryfall.com/search?q=" + quote(f'!"{card.name or ""}"')
            html_parts.append(f'<a href="{html_module.escape(scryfall_url)}" class="card-scryfall-link" target="_blank" rel="noopener">Scryfall</a>\n')
            html_parts.append('</article>\n')
        html_parts.append('</div>\n</section>\n')

    html_parts.append(
        """</div>
<script>
(function() {
  var setCheckboxes = Array.prototype.slice.call(document.querySelectorAll('input.set-cb'));
  var cards = Array.prototype.slice.call(document.querySelectorAll('.card-tile'));
  var sections = Array.prototype.slice.call(document.querySelectorAll('.commander-section'));
  function applySetFilter() {
    var selected = new Set(setCheckboxes.filter(function(cb) { return cb.checked; }).map(function(cb) { return (cb.value || '').toLowerCase(); }));
    cards.forEach(function(card) {
      var scode = (card.getAttribute('data-set-code') || '').toLowerCase();
      var show = selected.has(scode);
      card.classList.toggle('filtered-out', !show);
    });
    sections.forEach(function(sec) {
      var anyVisible = sec.querySelector('.card-tile:not(.filtered-out)');
      sec.classList.toggle('filtered-out', !anyVisible);
    });
  }
  setCheckboxes.forEach(function(cb) { cb.addEventListener('change', applySetFilter); });
  applySetFilter();
})();
</script>
</body>
</html>"""
    )

    out_path = args.new_cards_html or "outputs/new_cards.html"
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("".join(html_parts))
        print(f"Wrote new-cards summary to {out_path}")
    except OSError as e:
        print(f"Error writing new-cards HTML: {e}")


def run_new_cards_html(args):
    """Entry point for `--new-cards-html` (delegates to renderer module)."""
    from renderers.new_cards_renderer import run_new_cards_html as _render_new_cards_html

    _render_new_cards_html(args, impl_fn=_run_new_cards_html_impl)

def parse_card_list(filename):
    try:
        # Force UTF-8 so decklists with non-CP1252 characters load correctly on Windows.
        with open(filename, 'r', encoding='utf-8', errors='replace') as f:
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
