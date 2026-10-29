"""Five-score deck evaluator service (shared by the CLI and the web UI)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from edhparse.services import err_result, ok_result


def _default_out(out_dir: str, slug: str, ext: str) -> str:
    return str(Path(out_dir) / f"evaluate_{slug or 'deck'}.{ext}")


def run_evaluate(
    deck_path: Optional[str] = None,
    *,
    deck_text: Optional[str] = None,
    commander: Optional[str] = None,
    max_add_budget: float = 25.0,
    tuples: int = 8,
    hubs: int = 5,
    max_requests: int = 22,
    cache_ttl_days: float = 7.0,
    fresh_cache: bool = False,
    html_path: Optional[str] = "",
    md_path: Optional[str] = None,
    csv_path: Optional[str] = None,
    write_md: bool = False,
    write_csv: bool = False,
    out_dir: str = "outputs",
    session=None,
) -> dict:
    """Evaluate a 100-card deck: S1-S5 scores, cuts, adds, 1-for-1 swaps and land upgrades.

    ``html_path=""`` writes ``<out_dir>/evaluate_<slug>.html``; pass ``None`` to skip HTML.
    ``write_md`` / ``write_csv`` add the Markdown report / full score CSV next to it unless an
    explicit ``md_path`` / ``csv_path`` is given.
    """
    from clients.session import create_cached_session, ensure_scryfall_bulk
    from edhparse.evaluate import config as C
    from edhparse.evaluate.deck_parser import parse_decklist_file, parse_decklist_text
    from edhparse.evaluate.engine import EvaluationError, Evaluator
    from edhparse.evaluate.scryfall_meta import CardMetaLookup, manapool_price_lookup
    from renderers.evaluate_renderer import console_summary, write_reports

    if deck_text and deck_text.strip():
        deck = parse_decklist_text(deck_text, commander)
    elif deck_path:
        if not Path(deck_path).is_file():
            return err_result(f"Decklist not found: {deck_path}")
        deck = parse_decklist_file(deck_path, commander)
    else:
        return err_result("Provide a decklist file or paste a decklist.")
    for w in deck.warnings:
        print(f"Warning: {w}")

    session = session or create_cached_session(fresh_cache=fresh_cache)
    if not ensure_scryfall_bulk(session):
        return err_result("Scryfall bulk data is unavailable; cannot look up card metadata.")

    opts = C.EvaluateOptions(
        commander=commander or "",
        max_add_budget=float(max_add_budget),
        tuples=int(tuples),
        hubs=int(hubs),
        max_requests=int(max_requests),
        cache_ttl_days=float(cache_ttl_days),
        fresh_cache=fresh_cache,
    )
    metas = CardMetaLookup(price_lookup=manapool_price_lookup(session))
    try:
        result = Evaluator(session, opts, metas=metas).run(deck)
    except EvaluationError as exc:
        return err_result(str(exc))

    html_out = _default_out(out_dir, result.slug, "html") if html_path == "" else html_path
    md_out = md_path or (_default_out(out_dir, result.slug, "md") if write_md else "")
    csv_out = csv_path or (_default_out(out_dir, result.slug, "csv") if write_csv else "")
    outputs = write_reports(result, html_path=html_out or "", md_path=md_out, csv_path=csv_out)
    print(console_summary(result))
    return ok_result(
        f"Evaluated {' + '.join(result.commanders)}: {len(result.swaps)} swaps suggested "
        f"({result.telemetry.edhrec_requests} EDHREC requests, {result.telemetry.edhrec_cache_hits} cache hits).",
        outputs=outputs,
        result=result,
    )
