"""Collection summary: load commanders/lists, HTML/CSV/PDF/latest-set outputs."""

from __future__ import annotations

import sys
import time
from typing import Optional

from clients import scryfall_bulk
from clients.edhrec_prefetch import prefetch_edhrec
from clients.session import create_cached_session, ensure_scryfall_bulk
from core.card import SCRYFALL_SLEEP, _get_with_retry
from core.collection import Collection
from core.config import RunConfig, _ArgsShim
from core.stock import load_manabox
from edhparse.edhrec_util import edhrec_name_slug
from edhparse.parsing import load_commander_slugs_from_file, parse_card_list
from edhparse.services import err_result, ok_result
from utils.progress_utils import progress_bar


def print_list_with_totals(title: str, card_dict: dict):
    """Print a list of cards with prices and a total value."""
    print(f"\n--- {title} ---")
    total_value = 0.0

    sorted_cards = sorted(card_dict.items(), key=lambda item: item[1].get("price", 0.0), reverse=True)

    for name, data in sorted_cards:
        qty = data["quantity"]
        price = data.get("price", 0.0)
        total_card_price = qty * price
        total_value += total_card_price
        print(f"{qty}x {name}")
    print(f"\nTotal Value for {title}: ${total_value:.2f}")


def get_recent_sets(session, count=2):
    """Return the last `count` real sets from Scryfall (expansion/core/commander/draft_innovation)."""
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


# Back-compat alias
_get_recent_sets = get_recent_sets


def _export_pdf(html_path: str, pdf_path: str) -> bool:
    pdf_ok = False
    try:
        from weasyprint import HTML as WeasyHTML

        WeasyHTML(filename=html_path).write_pdf(pdf_path)
        pdf_ok = True
        print(f"Wrote PDF to {pdf_path}")
    except (ImportError, OSError, Exception) as e:
        if "libgobject" not in str(e):
            print(f"WeasyPrint failed: {e}")
        try:
            from xhtml2pdf import pisa

            with open(html_path, "r", encoding="utf-8") as html_file:
                html_src = html_file.read()
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
            with open(pdf_path, "w+b") as pdf_file:
                pisa_status = pisa.CreatePDF(html_src, dest=pdf_file, encoding="utf-8")
            if not pisa_status.err:
                pdf_ok = True
                print(f"Wrote PDF to {pdf_path} (via xhtml2pdf)")
            else:
                print("xhtml2pdf reported errors while writing PDF.")
        except ImportError:
            print("PDF export requires weasyprint or xhtml2pdf. On Windows: pip install xhtml2pdf")
        except Exception as e2:
            print(f"Error writing PDF: {e2}")
    return pdf_ok


def _prefetch_edhrec_pages(collection, names_and_types, commander, workers):
    """Warm the HTTP cache for every commander about to be built (see clients.edhrec_prefetch)."""
    if not workers or workers < 1:
        return
    base_names = [str(nt[0]).split("/")[0].strip() for nt in (names_and_types or [])]
    if commander:
        base_names.append(edhrec_name_slug(commander).split("/")[0].strip())
    stats = prefetch_edhrec(collection.session, base_names, workers=workers)
    if stats["to_fetch"]:
        print(
            f"EDHREC prefetch: {stats['fetched']} fetched, {stats['missing']} not on EDHREC, "
            f"{stats['errors']} errors in {stats['seconds']}s"
        )


def run_summary(
    cfg: RunConfig,
    *,
    commander: Optional[str] = None,
    commander_list: Optional[str] = None,
    card_list: Optional[str] = None,
    manabox: Optional[str] = None,
    write_csv: bool = False,
    html_path: Optional[str] = None,
    pdf_path: Optional[str] = None,
    latest_set_html: Optional[str] = None,
    prefetch_workers: int = 4,
) -> dict:
    """
    Load commanders and/or a decklist into a Collection, mark ownership, and emit reports.

    ``prefetch_workers``: EDHREC pages that aren't cached yet are fetched concurrently up front
    (0 disables this and falls back to fetching them one at a time while building).
    """
    args = _ArgsShim(cfg)
    collection = Collection(config=cfg, args=args)
    outputs = []

    names_and_types = None
    if commander_list:
        names_and_types = load_commander_slugs_from_file(commander_list)
        if names_and_types is None:
            return err_result("Failed to load commander list.")
        if not scryfall_bulk.is_available():
            print("Loading Scryfall bulk data...", flush=True)
            ensure_scryfall_bulk(create_cached_session(fresh_cache=cfg.fresh_cache))
        _prefetch_edhrec_pages(collection, names_and_types, commander, prefetch_workers)
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

    if commander:
        if not commander_list:
            _prefetch_edhrec_pages(collection, None, commander, prefetch_workers)
        collection.add_all_costs(just_name=edhrec_name_slug(commander))

    if card_list:
        list_cards = parse_card_list(card_list)
        collection.add_list_deck(list_cards)

    collection.lookup_deck_data()

    manabox_data = load_manabox(manabox) if manabox else None
    if manabox_data is not None:
        collection.mark_cards_owned(manabox_data)

    collection.print_collection()

    if write_csv:
        collection.write_to_file()
        outputs.append(cfg.csv_file or "csv")

    if html_path is not None:
        collection.write_summary_html(html_path, used_manabox=(manabox_data is not None))
        outputs.append(html_path)

    if pdf_path is not None:
        resolved_html = html_path if html_path is not None else (
            pdf_path.replace(".pdf", ".html") if pdf_path.endswith(".pdf") else "outputs/summary.html"
        )
        if html_path is None:
            collection.write_summary_html(resolved_html, used_manabox=(manabox_data is not None))
            outputs.append(resolved_html)
        if _export_pdf(resolved_html, pdf_path):
            outputs.append(pdf_path)

    if latest_set_html is not None:
        collection.write_latest_set_html(latest_set_html)
        outputs.append(latest_set_html)

    return ok_result("Collection summary complete", outputs=outputs)
