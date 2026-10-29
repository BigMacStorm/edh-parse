"""Convert-deck: compare old vs new lists against stock binders."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from clients import scryfall_bulk
from clients.session import create_cached_session, ensure_scryfall_bulk
from core.card import Card
from core.config import RunConfig, _ArgsShim
from core.deck_diff import (
    deck_to_canonical,
    diff_decks,
    generate_shopping_list,
    is_name_resolved,
    lookup_canonical,
    register_canonical_name,
)
from core.stock import StockCollection
from edhparse.parsing import parse_deck_file
from edhparse.services import err_result, ok_result
from utils.progress_utils import progress_bar

CURRENT_DECK_BINDER = "Current deck"


def price_from_scryfall_data(data) -> float:
    if data.get("type_line") is not None and "Basic Land" in str(data["type_line"]):
        return 0.0
    prices = data.get("prices") or {}
    if prices.get("usd") is not None:
        return float(prices["usd"])
    if prices.get("usd_foil") is not None:
        return float(prices["usd_foil"])
    return 0.0


# Back-compat alias used by other services / cli re-exports
_price_from_scryfall_data = price_from_scryfall_data


def _register_bulk_card(name, data, name_to_canonical, canonical_to_price):
    canon = data.get("name") or name
    register_canonical_name(name, canon, name_to_canonical)
    if canon not in canonical_to_price:
        canonical_to_price[canon] = price_from_scryfall_data(data)


def _resolve_names_via_bulk(names, name_to_canonical, canonical_to_price):
    """Resolve card names from the local Scryfall bulk cache (no API calls)."""
    for name in names:
        if is_name_resolved(name, name_to_canonical):
            continue
        data = scryfall_bulk.get_card_by_name_or_face(name)
        if data:
            _register_bulk_card(name, data, name_to_canonical, canonical_to_price)


def _owned_cards_by_binder_for_deck(binder_order, by_binder, canonical_new, name_to_canonical):
    """Allocate new-deck cards to binders in order, returning binder -> [(name, qty), ...]."""
    remaining = Counter(canonical_new)
    allocated = {binder: [] for binder in binder_order}

    for binder in binder_order:
        counter = by_binder.get(binder, Counter())
        binder_canonical = Counter()
        for name, qty in counter.items():
            binder_canonical[lookup_canonical(name, name_to_canonical)] += qty

        for canon in sorted(remaining.keys()):
            need = remaining[canon]
            if need <= 0:
                continue
            available = binder_canonical.get(canon, 0)
            if available <= 0:
                continue
            take = min(need, available)
            allocated[binder].append((canon, take))
            remaining[canon] -= take

    return allocated


def _sorted_convert_rows(items, sort_by, canonical_to_price):
    rows = [(name, qty, canonical_to_price.get(name, 0.0)) for name, qty in items]
    if sort_by == "name":
        rows.sort(key=lambda row: row[0].lower())
    else:
        rows.sort(key=lambda row: (-row[2], row[0].lower()))
    return rows


def _convert_card_label(name, qty):
    return f"{name} (x{qty})" if qty > 1 else name


def _print_convert_price_rows(rows, indent=""):
    total_value = 0.0
    if not rows:
        return total_value
    price_width = max(len(f"{price:.2f}") for _, _, price in rows)
    for name, qty, price in rows:
        line_total = qty * price
        total_value += line_total
        print(f"{indent}{price:>{price_width}.2f}  -  {_convert_card_label(name, qty)}")
    return total_value


def _print_convert_deck_changes(added, removed, sort_by, canonical_to_price):
    print("\n--- Deck Changes ---")
    if added:
        print("Cards added:")
        added_total = _print_convert_price_rows(
            _sorted_convert_rows(list(added.items()), sort_by, canonical_to_price),
            indent="  ",
        )
        print(f"  Subtotal: ${added_total:.2f}")
    else:
        print("Cards added: (none)")
    if removed:
        print("Cards removed:")
        removed_total = _print_convert_price_rows(
            _sorted_convert_rows(list(removed.items()), sort_by, canonical_to_price),
            indent="  ",
        )
        print(f"  Subtotal: ${removed_total:.2f}")
    else:
        print("Cards removed: (none)")


def _print_convert_need_to_buy(to_buy_priced, sort_by):
    print("\n--- Cards You Need to Buy ---")
    items = [
        (name, data["quantity"], data.get("price", 0.0))
        for name, data in to_buy_priced.items()
    ]
    rows = [(name, qty, price) for name, qty, price in items]
    if sort_by == "name":
        rows.sort(key=lambda row: row[0].lower())
    else:
        rows.sort(key=lambda row: (-row[2], row[0].lower()))

    total_value = _print_convert_price_rows(rows)
    print(f"\nTotal Value for Cards You Need to Buy: ${total_value:.2f}")


def _print_owned_by_binder(
    binder_order, by_binder, canonical_new, name_to_canonical, canonical_to_price, sort_by
):
    print("\n--- Cards You Have (from binders) ---")
    allocated = _owned_cards_by_binder_for_deck(
        binder_order, by_binder, canonical_new, name_to_canonical
    )
    grand_total = 0.0
    any_cards = False

    for binder in binder_order:
        cards = allocated.get(binder, [])
        if not cards:
            continue
        any_cards = True
        print(f"\n{binder}:")
        binder_value = _print_convert_price_rows(
            _sorted_convert_rows(cards, sort_by, canonical_to_price),
            indent="  ",
        )
        print(f"  Subtotal: ${binder_value:.2f}")
        grand_total += binder_value

    if not any_cards:
        print("(none)")
    else:
        print(f"\nTotal Value for Cards You Have: ${grand_total:.2f}")


def run_convert_deck(
    old_path: str | Path,
    new_path: str | Path,
    stock_csv: str | Path,
    binders: Sequence[str],
    cfg: RunConfig,
) -> dict:
    """Compare old vs new deck lists and report stock coverage plus buy cost for the new deck."""
    old_path = Path(old_path)
    new_path = Path(new_path)

    if not old_path.is_file():
        msg = f"Error: Old deck file not found: {old_path}"
        print(msg)
        return err_result(msg)
    if not new_path.is_file():
        msg = f"Error: New deck file not found: {new_path}"
        print(msg)
        return err_result(msg)

    old_deck = parse_deck_file(old_path)
    new_deck = parse_deck_file(new_path)

    stock = StockCollection.load(stock_csv, binders)
    if stock.error:
        print(f"Error: {stock.error}")
        return err_result(stock.error)

    binder_order, by_binder = stock.quantities_by_binder()
    by_binder = {CURRENT_DECK_BINDER: Counter(old_deck), **by_binder}
    binder_order = [CURRENT_DECK_BINDER] + binder_order
    owned_quantities: Counter = Counter()
    for counter in by_binder.values():
        owned_quantities.update(counter)
    owned_quantities_dict = dict(owned_quantities)

    session = create_cached_session(fresh_cache=cfg.fresh_cache)
    ensure_scryfall_bulk(session, fresh_cache=False)

    name_to_canonical: Dict[str, str] = {}
    canonical_to_price: Dict[str, float] = {}
    args = _ArgsShim(cfg)

    print("Resolving cards from bulk cache...", flush=True)
    _resolve_names_via_bulk(old_deck.keys(), name_to_canonical, canonical_to_price)
    _resolve_names_via_bulk(owned_quantities_dict.keys(), name_to_canonical, canonical_to_price)
    _resolve_names_via_bulk(new_deck.keys(), name_to_canonical, canonical_to_price)

    missing_names = {
        name
        for name in set(old_deck) | set(new_deck) | set(owned_quantities_dict)
        if not is_name_resolved(name, name_to_canonical)
    }
    if missing_names:
        print(f"Looking up {len(missing_names)} cards not in bulk cache via Scryfall API...")
        with progress_bar(len(missing_names)) as bar:
            for i, name in enumerate(sorted(missing_names)):
                bar.update(i, info=f"Fetching {name}")
                card = Card(session, args)
                card.search_card(name, fetch_alternate_names=False)
                if not card.error:
                    register_canonical_name(name, card.name, name_to_canonical)
                    if card.name not in canonical_to_price:
                        canonical_to_price[card.name] = card.price

    canonical_old = deck_to_canonical(old_deck, name_to_canonical)
    canonical_new = deck_to_canonical(new_deck, name_to_canonical)
    canonical_owned = deck_to_canonical(owned_quantities_dict, name_to_canonical)
    added, removed = diff_decks(canonical_old, canonical_new)

    _, owned_from_needed, to_buy = generate_shopping_list(
        dict(canonical_new), dict(canonical_owned)
    )

    sort_by = cfg.convert_sort or "price"

    print(f"\nConverting: {old_path.name} -> {new_path.name}")
    _print_convert_deck_changes(added, removed, sort_by, canonical_to_price)

    to_buy_priced = {
        name: {"quantity": qty, "price": canonical_to_price.get(name, 0.0)}
        for name, qty in to_buy.items()
    }

    _print_owned_by_binder(
        binder_order,
        by_binder,
        canonical_new,
        name_to_canonical,
        canonical_to_price,
        sort_by,
    )
    _print_convert_need_to_buy(to_buy_priced, sort_by)

    print("\n--- Summary ---")
    print(f"  New deck size: {sum(canonical_new.values())} cards")
    print(f"  Already available: {sum(owned_from_needed.values())} cards")
    print(f"  Still need to buy: {sum(to_buy.values())} cards")

    return ok_result(
        f"Converted {old_path.name} -> {new_path.name}",
        need_count=sum(to_buy.values()),
        owned_count=sum(owned_from_needed.values()),
    )
