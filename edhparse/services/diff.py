"""Diff directory mode: compare *_old / *_new deck pairs against ManaBox stock."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Optional

from clients.session import create_cached_session
from core.card import Card
from core.config import RunConfig, _ArgsShim
from core.deck_diff import diff_decks, generate_shopping_list
from core.stock import load_manabox
from edhparse.parsing import parse_deck_file
from edhparse.services import err_result, ok_result
from edhparse.services.summary import print_list_with_totals
from utils.progress_utils import progress_bar


def run_diff_directory(
    diff_dir: str | Path,
    manabox_csv: str | Path,
    cfg: Optional[RunConfig] = None,
) -> dict:
    """Analyze matching *_old / *_new pairs in a directory and print shopping lists."""
    cfg = cfg or RunConfig()
    diff_path = Path(diff_dir)
    if not diff_path.is_dir():
        msg = f"Error: --diff argument '{diff_dir}' is not a valid directory."
        print(msg)
        return err_result(msg)

    old_files = {p.stem.replace("_old", ""): p for p in diff_path.glob("*_old")}
    new_files = {p.stem.replace("_new", ""): p for p in diff_path.glob("*_new")}

    pairs = []
    for name, old_path in old_files.items():
        if name in new_files:
            pairs.append((old_path, new_files[name]))

    if not pairs:
        msg = f"No matching '_old' and '_new' file pairs found in '{diff_dir}'."
        print(msg)
        return err_result(msg)

    aggregated_needed = Counter()
    diff_results = []

    print("Analyzing deck pairs...")
    for old_path, new_path in pairs:
        try:
            old_deck = parse_deck_file(old_path)
            new_deck = parse_deck_file(new_path)
            added, removed = diff_decks(old_deck, new_deck)

            diff_results.append(
                {
                    "name": old_path.stem.replace("_old", ""),
                    "added": added,
                    "removed": removed,
                }
            )
            aggregated_needed.update(added)
        except Exception as e:
            print(f"\nError processing pair {old_path.name}/{new_path.name}: {e}")

    print("\nFetching card data...")
    owned_quantities = load_manabox(manabox_csv, as_dict=True)
    if owned_quantities is None:
        return err_result("Failed to load ManaBox CSV.")

    session = create_cached_session(fresh_cache=cfg.fresh_cache)
    args = _ArgsShim(cfg)

    name_to_canonical = {}
    canonical_to_price = {}

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

    for result in diff_results:
        print(f"\n--- Diff for {result['name']} ---")
        print("Cards Added:")
        for name, qty in result["added"].items():
            print(f"  {qty}x {name}")
        print("Cards Removed:")
        for name, qty in result["removed"].items():
            print(f"  {qty}x {name}")

        canonical_added = Counter()
        for name, qty in result["added"].items():
            if name in name_to_canonical:
                canonical_name = name_to_canonical[name]
                canonical_added[canonical_name] += qty

        _, _, to_buy_deck = generate_shopping_list(canonical_added, canonical_owned)

        total_cost_new = sum(
            canonical_to_price.get(name, 0.0) * qty for name, qty in canonical_added.items()
        )
        cost_to_buy = sum(
            canonical_to_price.get(name, 0.0) * qty for name, qty in to_buy_deck.items()
        )

        print("\nRecap:")
        print(f"  Cards to replace: {sum(result['added'].values())}")
        print(f"  Total cost of new cards: ${total_cost_new:.2f}")
        print(f"  Cost factoring in owned cards: ${cost_to_buy:.2f}\n")

    print("\n--- Overall Shopping List ---")
    canonical_needed = Counter()
    for name, qty in aggregated_needed.items():
        if name in name_to_canonical:
            canonical_name = name_to_canonical[name]
            canonical_needed[canonical_name] += qty

    all_needed, owned_from_needed, to_buy = generate_shopping_list(
        canonical_needed, canonical_owned
    )

    all_needed_priced = {
        name: {"quantity": qty, "price": canonical_to_price.get(name, 0.0)}
        for name, qty in all_needed.items()
    }
    owned_from_needed_priced = {
        name: {"quantity": qty, "price": canonical_to_price.get(name, 0.0)}
        for name, qty in owned_from_needed.items()
    }
    to_buy_priced = {
        name: {"quantity": qty, "price": canonical_to_price.get(name, 0.0)}
        for name, qty in to_buy.items()
    }

    print_list_with_totals("All Cards to be Added", all_needed_priced)
    print_list_with_totals("Cards Owned", owned_from_needed_priced)
    print_list_with_totals("Cards to Buy", to_buy_priced)

    return ok_result(
        f"Diffed {len(pairs)} deck pairs",
        pair_count=len(pairs),
        to_buy_count=sum(to_buy.values()),
    )
