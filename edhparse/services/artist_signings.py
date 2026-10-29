"""Artist signings: match stock printings to convention artist list."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional, Sequence

from clients import scryfall_bulk
from clients.session import create_cached_session
from core.card import Card, _get_with_retry
from core.config import RunConfig, _ArgsShim
from core.stock import load_stock_rows_for_binders
from edhparse.parsing import parse_artist_names_file
from edhparse.services import err_result, ok_result
from renderers.artist_signings_renderer import write_artist_signings_html
from utils.progress_utils import progress_bar


def _image_from_scryfall_data(data):
    """Return a reasonable image URL for this printing."""
    if not isinstance(data, dict):
        return None
    image_uris = data.get("image_uris") or {}
    for key in ("large", "normal", "png", "small"):
        if image_uris.get(key):
            return image_uris.get(key)
    faces = data.get("card_faces") or []
    for face in faces:
        if not isinstance(face, dict):
            continue
        face_images = face.get("image_uris") or {}
        for key in ("large", "normal", "png", "small"):
            if face_images.get(key):
                return face_images.get(key)
    return None


def run_artist_signings(
    stock_csv: str | Path,
    binder_names: Sequence[str],
    artists_txt: str | Path,
    cfg: Optional[RunConfig] = None,
    *,
    out_path: str = "outputs/artist_signings.html",
) -> dict:
    """
    Find cards in selected binders whose Scryfall artist matches an artist in the provided text file.
    Output is grouped by artist, with cards sorted by value (highest first).
    """
    cfg = cfg or RunConfig()
    stock_path = Path(stock_csv)
    if not stock_path.is_file():
        msg = f"Error: Stock CSV file not found: {stock_csv}"
        print(msg)
        return err_result(msg)
    artist_path = Path(artists_txt)
    if not artist_path.is_file():
        msg = f"Error: Artist list file not found: {artists_txt}"
        print(msg)
        return err_result(msg)

    artist_names = parse_artist_names_file(artist_path)
    if not artist_names:
        msg = "No artist names found in the artist list file."
        print(msg)
        return err_result(msg)
    artist_lookup = {(name or "").strip().lower(): name for name in artist_names}

    stock_rows = load_stock_rows_for_binders(stock_csv, binder_names)
    if stock_rows is None:
        return err_result("Failed to load stock rows.")
    if not stock_rows:
        msg = "No matching cards found in the selected binders."
        print(msg)
        return err_result(msg)

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

    session = create_cached_session(fresh_cache=cfg.fresh_cache)
    args = _ArgsShim(cfg)

    def lookup_printing(row_name, row_set, row_collector, row_scryfall_id):
        """Lookup artist/price/image for an exact printing, with graceful fallback."""
        if row_scryfall_id:
            data = scryfall_bulk.get_card_by_id(row_scryfall_id)
            if data is not None:
                return data.get("artist"), Card(session, args).get_price(data), _image_from_scryfall_data(data)
            url = f"https://api.scryfall.com/cards/{row_scryfall_id}"
            response, err = _get_with_retry(session, url, params=None, timeout=8, max_retries=5)
            if not err and response is not None:
                d = response.json()
                return d.get("artist"), Card(session, args).get_price(d), _image_from_scryfall_data(d)

        if row_set and row_collector:
            url = f"https://api.scryfall.com/cards/{row_set.lower()}/{row_collector}"
            response, err = _get_with_retry(session, url, params=None, timeout=8, max_retries=5)
            if not err and response is not None:
                d = response.json()
                return d.get("artist"), Card(session, args).get_price(d), _image_from_scryfall_data(d)

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
        msg = "No cards matched the provided artist list."
        print(msg)
        return err_result(msg)

    write_artist_signings_html(out_path, grouped, list(binder_names))
    print(f"Wrote artist signings report to {out_path}")
    return ok_result(f"Wrote artist signings report to {out_path}", outputs=[out_path])
