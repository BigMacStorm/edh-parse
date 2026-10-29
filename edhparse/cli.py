"""Argparse CLI entrypoint. Business logic lives in edhparse.services.*."""

from __future__ import annotations

import argparse
import sys

from clients.session import create_cached_session, ensure_scryfall_bulk
from core.config import RunConfig
from core.stock import (
    load_manabox,
    load_owned_quantities_by_binder_from_stock_csv,
    load_owned_quantities_from_stock_csv,
    load_stock_rows_for_binders,
)
from edhparse.edhrec_util import edhrec_name_slug
from edhparse.parsing import (
    load_commander_slugs_from_file,
    parse_artist_names_file,
    parse_card_list,
    parse_card_names_file,
    parse_deck_file,
)
from edhparse.services.artist_signings import run_artist_signings as _run_artist_signings
from edhparse.services.compare import run_compare
from edhparse.services.convert import (
    CURRENT_DECK_BINDER,
    _convert_card_label,
    _owned_cards_by_binder_for_deck,
    _print_convert_deck_changes,
    _print_convert_need_to_buy,
    _print_convert_price_rows,
    _print_owned_by_binder,
    _price_from_scryfall_data,
    _register_bulk_card,
    _resolve_names_via_bulk,
    _sorted_convert_rows,
    run_convert_deck as _run_convert_deck,
)
from edhparse.services.diff import run_diff_directory
from edhparse.services.locate import locate_cards_in_stock, run_locate
from edhparse.services.new_cards import run_new_cards_html as _run_new_cards_html
from edhparse.services.pivot import run_pivot_html as _run_pivot_html
from edhparse.services.safe_shopping import (
    load_owned_names_from_stock_csv as _load_owned_names_from_stock_csv,
    run_safe_shopping,
)
from edhparse.services.summary import (
    _get_recent_sets,
    print_list_with_totals,
    run_summary,
)
from renderers.artist_signings_renderer import write_artist_signings_html

# pylint: disable=missing-function-docstring, missing-class-docstring

# Back-compat aliases expected by tests / older imports
_load_owned_quantities_by_binder_from_stock_csv = load_owned_quantities_by_binder_from_stock_csv
_load_owned_quantities_from_stock_csv = load_owned_quantities_from_stock_csv
get_edhrec_name = edhrec_name_slug
load_urls_from_file = load_commander_slugs_from_file


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
    parser.add_argument(
        "--max-themes",
        type=int,
        default=5,
        metavar="N",
        help="With --new-cards-html: EDHREC archetype tags after the Average hub (default: 5).",
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
    parser.add_argument(
        "--convert-deck",
        nargs=2,
        metavar=("OLD", "NEW"),
        help="Compare an old deck list to a new target deck using stock CSV ownership.",
    )
    parser.add_argument(
        "--convert-stock-csv",
        metavar="FILE",
        help="With --convert-deck: stock/ManaBox CSV for ownership lookup.",
    )
    parser.add_argument(
        "--convert-binders",
        nargs="+",
        metavar="BINDER",
        help="With --convert-deck: binder names to count as owned (case-insensitive).",
    )
    parser.add_argument(
        "--convert-sort",
        choices=("price", "name"),
        default="price",
        help="With --convert-deck: sort all card lists (added, removed, owned, need to buy) by price or name.",
    )
    parser.add_argument(
        "--pivot-html",
        nargs="?",
        const="outputs/pivot.html",
        metavar="FILE",
        help=(
            "Suggest alternate commanders from expensive cards in a decklist (-l/--list). "
            "Looks up each card ≥ --pivot-min-price on EDHREC Top Commanders and aggregates "
            "inclusion% weighted by price. Default: outputs/pivot.html"
        ),
    )
    parser.add_argument(
        "--pivot-min-price",
        type=float,
        default=5.0,
        metavar="USD",
        help="With --pivot-html: only score cards priced at or above this amount (default: 5).",
    )
    parser.add_argument(
        "--pivot-min-inclusion-pct",
        type=float,
        default=0.0,
        metavar="PCT",
        help="With --pivot-html: ignore commander hits below this inclusion percent when aggregating (default: 0).",
    )
    parser.add_argument(
        "--pivot-top",
        type=int,
        default=40,
        metavar="N",
        help="With --pivot-html: only write the top N commanders by score (default: 40).",
    )
    return parser.parse_args()


def run_config_from_args(args) -> RunConfig:
    """Map argparse Namespace fields onto RunConfig."""
    return RunConfig(
        fresh_cache=bool(getattr(args, "fresh_cache", False)),
        show_owned=bool(getattr(args, "show_owned", False)),
        need=bool(getattr(args, "need", False)),
        show_all=bool(getattr(args, "all", False)),
        csv_file=getattr(args, "csv_file", None),
        stock_csv=getattr(args, "manabox", None),
        max_themes=int(getattr(args, "max_themes", 5) or 5),
        safe_shopping_min_inclusion_pct=float(
            getattr(args, "safe_shopping_min_inclusion_pct", 20.0) or 0.0
        ),
        safe_shopping_min_synergy_pct=float(
            getattr(args, "safe_shopping_min_synergy_pct", 20.0) or 0.0
        ),
        safe_shopping_min_price=float(getattr(args, "safe_shopping_min_price", 0.0) or 0.0),
        safe_shopping_sort_default=getattr(args, "safe_shopping_sort_default", None) or "inclusion",
        pivot_min_price=float(getattr(args, "pivot_min_price", 5.0) if getattr(args, "pivot_min_price", None) is not None else 5.0),
        pivot_min_inclusion_pct=float(getattr(args, "pivot_min_inclusion_pct", 0.0) or 0.0),
        pivot_top=int(getattr(args, "pivot_top", 40) if getattr(args, "pivot_top", None) is not None else 40),
        compare_min_inclusion_pct=getattr(args, "compare_edhrec_min_inclusion_pct", None),
        convert_sort=getattr(args, "convert_sort", None) or "price",
    )


def run_convert_deck(args):
    """CLI wrapper: Namespace → typed convert service."""
    cfg = run_config_from_args(args)
    return _run_convert_deck(
        args.convert_deck[0],
        args.convert_deck[1],
        args.convert_stock_csv,
        args.convert_binders or [],
        cfg,
    )


def run_artist_signings(stock_csv, binder_names, artists_txt, args):
    cfg = run_config_from_args(args)
    return _run_artist_signings(stock_csv, binder_names, artists_txt, cfg)


def run_safe_shopping_html(args):
    cfg = run_config_from_args(args)
    return run_safe_shopping(
        args.commander_list,
        args.safe_shopping_html or "outputs/safe_shopping.html",
        cfg,
        stock_csv=args.safe_shopping_stock_csv,
        binders=args.safe_shopping_binders,
    )


def _run_new_cards_html_impl(args):
    cfg = run_config_from_args(args)
    return _run_new_cards_html(
        args.commander_list,
        args.new_cards_html or "outputs/new_cards.html",
        cfg,
    )


def run_new_cards_html(args):
    """Entry point for `--new-cards-html`."""
    return _run_new_cards_html_impl(args)


def run_pivot_html(args):
    cfg = run_config_from_args(args)
    return _run_pivot_html(
        args.list,
        args.pivot_html or "outputs/pivot.html",
        cfg,
    )


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = build_args()
    cfg = run_config_from_args(args)

    startup_session = create_cached_session(fresh_cache=args.fresh_cache)
    print("Checking Scryfall bulk data...", flush=True)
    if not ensure_scryfall_bulk(startup_session):
        print(
            "Warning: could not refresh/load Scryfall bulk data. "
            "Lookups will fall back to cache/live API where needed."
        )

    if getattr(args, "compare_edhrec", None):
        url_a, url_b = args.compare_edhrec
        run_compare(
            url_a,
            url_b,
            cfg,
            session=startup_session,
            min_inclusion_both_pct=args.compare_edhrec_min_inclusion_pct,
        )
        return

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

    if getattr(args, "safe_shopping_html", None) is not None:
        if not args.commander_list:
            print("Error: --safe-shopping-html requires --commander_list pointing to your commander list file.")
            return
        if args.safe_shopping_stock_csv and not args.safe_shopping_binders:
            print("Error: --safe-shopping-stock-csv requires --safe-shopping-binders.")
            return
        run_safe_shopping_html(args)
        return

    if getattr(args, "new_cards_html", None) is not None:
        if not args.commander_list:
            print("Error: --new-cards-html requires --commander_list pointing to your commander list file.")
            return
        run_new_cards_html(args)
        return

    if getattr(args, "pivot_html", None) is not None:
        if not args.list:
            print("Error: --pivot-html requires -l/--list pointing to a decklist file.")
            return
        run_pivot_html(args)
        return

    if args.convert_deck:
        if not args.convert_stock_csv or not args.convert_binders:
            print("Error: --convert-deck requires --convert-stock-csv and --convert-binders.")
            return
        run_convert_deck(args)
        return

    if args.diff:
        if not args.manabox:
            print("Error: The --diff feature requires a ManaBox CSV file specified with --manabox.")
            return
        run_diff_directory(args.diff, args.manabox, cfg)
        return

    if args.locate:
        if not args.manabox:
            print("Error: The --locate feature requires a stock/Manabox CSV file specified with --manabox.")
            return
        run_locate(args.locate, args.manabox, cfg)
        return

    run_summary(
        cfg,
        commander=args.commander,
        commander_list=args.commander_list,
        card_list=args.list,
        manabox=args.manabox,
        write_csv=bool(args.csv),
        html_path=args.html,
        pdf_path=getattr(args, "pdf", None),
        latest_set_html=getattr(args, "latest_set_html", None),
    )


if __name__ == "__main__":
    main()
