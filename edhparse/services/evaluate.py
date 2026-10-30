"""Five-score deck evaluator service (shared by the CLI and the web UI)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, Union

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
        p = Path(deck_path)
        if p.is_dir():
            return run_evaluate_batch(
                p,
                max_add_budget=max_add_budget,
                tuples=tuples,
                hubs=hubs,
                max_requests=max_requests,
                cache_ttl_days=cache_ttl_days,
                fresh_cache=fresh_cache,
                write_md=write_md,
                write_csv=write_csv,
                out_dir=out_dir,
                session=session,
            )
        if not p.is_file():
            return err_result(f"Decklist not found: {deck_path}")
        deck = parse_decklist_file(deck_path, commander)
    else:
        return err_result("Provide a decklist file or paste a decklist.")
    for w in deck.warnings:
        print(f"Warning: {w}")

    session = session or create_cached_session(fresh_cache=fresh_cache)
    if not ensure_scryfall_bulk(session):
        return err_result("Scryfall bulk data is unavailable; cannot look up card metadata.")

    try:
        b_val = float(max_add_budget) if max_add_budget is not None else 0.0
    except (ValueError, TypeError):
        b_val = 0.0
    opts = C.EvaluateOptions(
        commander=commander or "",
        max_add_budget=b_val if b_val > 0 else 0.0,
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


def run_evaluate_batch(
    folder: Union[str, Path] = "inputs/moxfield",
    *,
    max_add_budget: float = 25.0,
    tuples: int = 8,
    hubs: int = 5,
    max_requests: int = 22,
    cache_ttl_days: float = 7.0,
    fresh_cache: bool = False,
    write_md: bool = False,
    write_csv: bool = False,
    out_dir: str = "outputs",
    session=None,
) -> dict:
    """Evaluate every decklist (.txt) in folder and generate individual and summary reports."""
    from clients.session import create_cached_session, ensure_scryfall_bulk
    from edhparse.evaluate import config as C
    from edhparse.evaluate.deck_parser import parse_decklist_file
    from edhparse.evaluate.engine import Evaluator
    from edhparse.evaluate.scryfall_meta import CardMetaLookup, manapool_price_lookup
    from renderers.evaluate_renderer import render_batch_summary_html, write_reports
    from utils.progress_utils import notify_progress

    target_dir = Path(folder)
    if not target_dir.is_dir():
        return err_result(f"Directory not found: {folder}. Please grab favorites first.")

    files = sorted(target_dir.glob("*.txt"))
    if not files:
        return err_result(f"No .txt deck files found in {folder}. Please grab favorites first.")

    session = session or create_cached_session(fresh_cache=fresh_cache)
    if not ensure_scryfall_bulk(session):
        return err_result("Scryfall bulk data is unavailable; cannot look up card metadata.")

    try:
        b_val = float(max_add_budget) if max_add_budget is not None else 0.0
    except (ValueError, TypeError):
        b_val = 0.0

    metas = CardMetaLookup(price_lookup=manapool_price_lookup(session))
    total = len(files)
    notify_progress(0, total, f"Starting batch evaluation for {total} decks in {target_dir.name}...")
    print(f"Batch evaluating {total} decks from {folder}...")

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    all_outputs: List[str] = []
    summaries: List[dict] = []

    for idx, file_path in enumerate(files, 1):
        deck_name = file_path.stem.replace("-", " ").title()
        notify_progress(idx - 1, total, f"[{idx}/{total}] Evaluating {deck_name}...")
        print(f"\n{'=' * 60}\n[{idx}/{total}] Evaluating deck: {file_path.name}...\n{'=' * 60}")
        try:
            deck = parse_decklist_file(file_path)
            for w in deck.warnings:
                print(f"Warning: {w}")
            opts = C.EvaluateOptions(
                commander="",
                max_add_budget=b_val if b_val > 0 else 0.0,
                tuples=int(tuples),
                hubs=int(hubs),
                max_requests=int(max_requests),
                cache_ttl_days=float(cache_ttl_days),
                fresh_cache=fresh_cache,
            )
            res = Evaluator(session, opts, metas=metas, log=print).run(deck)
            html_out = _default_out(out_dir, res.slug, "html")
            md_out = _default_out(out_dir, res.slug, "md") if write_md else ""
            csv_out = _default_out(out_dir, res.slug, "csv") if write_csv else ""
            written = write_reports(res, html_path=html_out, md_path=md_out, csv_path=csv_out)
            all_outputs.extend(written)
            summaries.append({
                "file": file_path.name,
                "title": deck_name,
                "commanders": res.commanders,
                "slug": res.slug,
                "html_path": html_out,
                "swaps": len(res.swaps),
                "cuts": len(res.cuts),
                "adds": len(res.adds),
                "edhrec_requests": res.telemetry.edhrec_requests,
                "cache_hits": res.telemetry.edhrec_cache_hits,
                "status": "ok",
            })
            print(f"Done [{idx}/{total}] {file_path.name}: {len(res.swaps)} swaps suggested.")
        except Exception as exc:
            print(f"Failed [{idx}/{total}] {file_path.name}: {exc}")
            summaries.append({
                "file": file_path.name,
                "title": deck_name,
                "commanders": [],
                "slug": "",
                "html_path": "",
                "swaps": 0,
                "cuts": 0,
                "adds": 0,
                "edhrec_requests": 0,
                "cache_hits": 0,
                "status": f"Error: {exc}",
            })
        notify_progress(idx, total, f"[{idx}/{total}] Finished {deck_name}")

    summary_html_path = str(Path(out_dir) / "evaluate_batch_summary.html")
    Path(summary_html_path).write_text(render_batch_summary_html(summaries, folder=str(folder)), encoding="utf-8")
    all_outputs.insert(0, summary_html_path)

    notify_progress(total, total, f"Completed batch evaluation of {total} decks.")
    successful = [s for s in summaries if s.get("status") == "ok"]
    return ok_result(
        f"Batch evaluated {len(successful)}/{total} decks successfully from {folder}.",
        outputs=all_outputs,
        summaries=summaries,
    )
