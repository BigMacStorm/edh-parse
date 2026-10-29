"""Pivot HTML: suggest alternate commanders from expensive cards in a decklist."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from clients import scryfall_bulk
from clients.session import create_cached_session
from core.card import Card
from core.config import RunConfig, _ArgsShim
from edhparse.parsing import parse_card_list
from edhparse.pivot import (
    PricedCard,
    aggregate_commander_scores,
    build_pivot_report,
    fetch_top_commanders_for_card,
    select_expensive_cards,
)
from edhparse.services import err_result, ok_result
from edhparse.services.convert import price_from_scryfall_data
from utils.progress_utils import progress_bar


def run_pivot_html(
    list_path: str | Path,
    out_path: str,
    cfg: RunConfig,
) -> dict:
    """
    Suggest alternate commanders based on expensive cards already in a decklist.

    Uses Scryfall prices (bulk) + EDHREC card pages (Top Commanders), then writes HTML.
    """
    from renderers.pivot_renderer import write_pivot_html

    list_path = Path(list_path)
    if not list_path.is_file():
        msg = f"Error: decklist file not found: {list_path}"
        print(msg)
        return err_result(msg)

    card_data = parse_card_list(str(list_path))
    if not card_data:
        msg = "Error: no cards found in decklist."
        print(msg)
        return err_result(msg)

    excluded_commander = None
    priced: list = []
    session = create_cached_session(fresh_cache=cfg.fresh_cache)
    args = _ArgsShim(cfg)

    for name, meta in card_data.items():
        header = (meta.get("header") or "mainboard").lower()
        qty = int(meta.get("quantity") or 1)
        if header == "commander":
            excluded_commander = name
            continue
        if header == "sideboard":
            continue
        data = scryfall_bulk.get_card_by_name_or_face(name)
        if data:
            price = price_from_scryfall_data(data)
            type_line = data.get("type_line") or ""
            canon = data.get("name") or name
        else:
            c = Card(session, args, is_commander=False)
            c.search_card(name)
            if c.error:
                print(f"Warning: could not price card: {name}")
                continue
            price = float(c.price or 0.0)
            type_line = c.type_line or ""
            canon = c.name or name
        priced.append(
            PricedCard(
                name=canon,
                price=float(price or 0.0),
                quantity=max(1, qty),
                type_line=type_line,
            )
        )

    min_price = float(cfg.pivot_min_price if cfg.pivot_min_price is not None else 5.0)
    expensive = select_expensive_cards(priced, min_price=min_price, skip_lands=True)
    if not expensive:
        msg = f"No cards priced at or above ${min_price:.2f}. Nothing to pivot."
        print(msg)
        return err_result(msg)

    print(
        f"Scoring {len(expensive)} expensive cards (≥ ${min_price:.2f}) "
        f"worth ${sum(c.price * c.quantity for c in expensive):.2f}..."
    )

    card_commanders = []
    errors = 0
    with progress_bar(len(expensive), initial_value=0) as bar:
        for idx, card in enumerate(expensive):
            bar.update(idx, info=f"EDHREC commanders for {card.name}")
            hits, err = fetch_top_commanders_for_card(session, card.name)
            if err:
                errors += 1
                print(f"Warning: EDHREC lookup failed for {card.name}: {err}")
            card_commanders.append((card, hits))
        bar.update(len(expensive), info="EDHREC lookups done")

    min_inclusion = float(cfg.pivot_min_inclusion_pct or 0.0)
    exclude_names = [excluded_commander] if excluded_commander else []
    aggregates = aggregate_commander_scores(
        card_commanders,
        exclude_commander_names=exclude_names,
        min_inclusion_pct=min_inclusion,
    )
    top_n = cfg.pivot_top if cfg.pivot_top is not None else 40
    report = build_pivot_report(
        source_label=str(list_path),
        expensive_cards=expensive,
        aggregates=aggregates,
        excluded_commander=excluded_commander,
        min_price=min_price,
        top_n=top_n,
    )

    resolved_out = out_path or "outputs/pivot.html"
    out = Path(resolved_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        write_pivot_html(out_path=str(out), report=report)
    except OSError as e:
        msg = f"Error writing pivot HTML: {e}"
        print(msg)
        return err_result(msg)

    msg = (
        f"Wrote {out} with {report['commander_count']} commanders "
        f"from {report['expensive_card_count']} expensive cards"
        + (f" ({errors} EDHREC lookup warnings)." if errors else ".")
    )
    print(msg)
    return ok_result(msg, outputs=[str(out)])
