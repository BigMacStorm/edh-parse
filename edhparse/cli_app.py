"""Typer CLI — named subcommands wrapping edhparse.services."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

import typer

from clients.session import create_cached_session, ensure_scryfall_bulk
from core.config import DEFAULT_SUMMARY_BINDERS, RunConfig

app = typer.Typer(
    name="edhparse",
    help="EDH deck tooling: EDHREC averages, Scryfall pricing, stock ownership, HTML reports.",
    no_args_is_help=True,
    add_completion=False,
)
wipcoms_app = typer.Typer(help="WIP commander pipeline (fetch EDHREC dumps + singletonize).")
app.add_typer(wipcoms_app, name="wipcoms")


def _fail(result: dict) -> None:
    msg = result.get("error") or result.get("message") or "Command failed"
    typer.echo(msg, err=True)
    raise typer.Exit(code=1)


def _finish(result: dict) -> None:
    if not result.get("ok"):
        _fail(result)
    msg = result.get("message")
    if msg:
        typer.echo(msg)
    for path in result.get("outputs") or []:
        typer.echo(f"  → {path}")


def _cfg(
    *,
    fresh_cache: bool = False,
    show_owned: bool = False,
    need: bool = False,
    show_all: bool = False,
    csv_file: Optional[str] = None,
    binders: Optional[List[str]] = None,
    max_themes: int = 5,
    safe_shopping_min_inclusion_pct: float = 20.0,
    safe_shopping_min_synergy_pct: float = 20.0,
    safe_shopping_min_price: float = 0.0,
    safe_shopping_sort_default: str = "inclusion",
    pivot_min_price: float = 5.0,
    pivot_min_inclusion_pct: float = 0.0,
    pivot_top: int = 40,
    compare_min_inclusion_pct: Optional[float] = None,
    convert_sort: str = "price",
) -> RunConfig:
    return RunConfig(
        fresh_cache=fresh_cache,
        show_owned=show_owned,
        need=need,
        show_all=show_all,
        csv_file=csv_file,
        binders=list(binders) if binders else list(DEFAULT_SUMMARY_BINDERS),
        max_themes=max_themes,
        safe_shopping_min_inclusion_pct=safe_shopping_min_inclusion_pct,
        safe_shopping_min_synergy_pct=safe_shopping_min_synergy_pct,
        safe_shopping_min_price=safe_shopping_min_price,
        safe_shopping_sort_default=safe_shopping_sort_default,
        pivot_min_price=pivot_min_price,
        pivot_min_inclusion_pct=pivot_min_inclusion_pct,
        pivot_top=pivot_top,
        compare_min_inclusion_pct=compare_min_inclusion_pct,
        convert_sort=convert_sort,
    )


@app.callback()
def _root(
    ctx: typer.Context,
    fresh_cache: bool = typer.Option(False, "--fresh-cache", help="Clear HTTP cache before running."),
):
    """Shared options."""
    ctx.ensure_object(dict)
    ctx.obj["fresh_cache"] = fresh_cache


@app.command("summary")
def cmd_summary(
    ctx: typer.Context,
    commander: Optional[str] = typer.Option(None, "--commander", "-c"),
    commanders: Optional[Path] = typer.Option(None, "--commanders", "-cl", help="File of commander names/URLs."),
    decklist: Optional[Path] = typer.Option(None, "--list", "-l", help="MTGO-format decklist."),
    stock: Optional[Path] = typer.Option(None, "--stock", "-mb", help="ManaBox/stock CSV."),
    show_owned: bool = typer.Option(False, "--show-owned", "-o"),
    need: bool = typer.Option(False, "--need", "-n"),
    show_all: bool = typer.Option(False, "--all"),
    csv: bool = typer.Option(False, "--csv"),
    csv_file: Optional[str] = typer.Option(None, "--csv-file", "-cf"),
    html: Optional[Path] = typer.Option(
        None,
        "--html",
        help="Write summary HTML (default path if flag passed with no value via =outputs/summary.html).",
    ),
    write_html: bool = typer.Option(False, "--write-html", help="Write outputs/summary.html"),
    pdf: Optional[str] = typer.Option(None, "--pdf"),
    latest_set: Optional[str] = typer.Option(None, "--latest-set-html"),
):
    """Load EDHREC average decks and/or a list; emit console/CSV/HTML/PDF."""
    from edhparse.services.summary import run_summary

    if not commander and not commanders and not decklist:
        typer.echo("Provide --commander, --commanders, and/or --list.", err=True)
        raise typer.Exit(1)

    html_path = None
    if write_html:
        html_path = "outputs/summary.html"
    if html is not None:
        html_path = str(html)

    cfg = _cfg(
        fresh_cache=ctx.obj.get("fresh_cache", False),
        show_owned=show_owned,
        need=need,
        show_all=show_all,
        csv_file=csv_file,
    )
    ensure_scryfall_bulk(create_cached_session(fresh_cache=cfg.fresh_cache))
    result = run_summary(
        cfg,
        commander=commander,
        commander_list=str(commanders) if commanders else None,
        card_list=str(decklist) if decklist else None,
        manabox=str(stock) if stock else None,
        write_csv=csv,
        html_path=html_path,
        pdf_path=pdf,
        latest_set_html=latest_set,
    )
    _finish(result)


@app.command("convert")
def cmd_convert(
    ctx: typer.Context,
    old_deck: Path = typer.Argument(..., help="Old decklist"),
    new_deck: Path = typer.Argument(..., help="New decklist"),
    stock: Path = typer.Option(..., "--stock", help="Stock CSV"),
    binders: List[str] = typer.Option(..., "--binders", help="Binder names counting as owned"),
    sort_by: str = typer.Option("price", "--sort", help="price|name"),
):
    """Compare old→new deck and report stock coverage / buy list."""
    from edhparse.services.convert import run_convert_deck

    cfg = _cfg(fresh_cache=ctx.obj.get("fresh_cache", False), convert_sort=sort_by)
    result = run_convert_deck(old_deck, new_deck, stock, binders, cfg)
    _finish(result)


@app.command("diff")
def cmd_diff(
    ctx: typer.Context,
    directory: Path = typer.Argument(..., help="Dir with *_old / *_new pairs"),
    stock: Path = typer.Option(..., "--stock", "-mb", help="ManaBox/stock CSV"),
):
    """Diff matching *_old / *_new deck pairs and aggregate shopping list."""
    from edhparse.services.diff import run_diff_directory

    cfg = _cfg(fresh_cache=ctx.obj.get("fresh_cache", False))
    result = run_diff_directory(directory, stock, cfg)
    _finish(result)


@app.command("locate")
def cmd_locate(
    ctx: typer.Context,
    names_file: Path = typer.Argument(..., help="Card names file"),
    stock: Path = typer.Option(..., "--stock", "-mb"),
):
    """Find cards from a names file in stock binders."""
    from edhparse.services.locate import run_locate

    cfg = _cfg(fresh_cache=ctx.obj.get("fresh_cache", False))
    result = run_locate(names_file, stock, cfg)
    _finish(result)


@app.command("find-in-binders")
def cmd_find_in_binders(
    ctx: typer.Context,
    names_file: Optional[Path] = typer.Argument(None, help="Card names file (optional if --paste)"),
    stock: Path = typer.Option(..., "--stock", "-mb"),
    paste: Optional[str] = typer.Option(None, "--paste", "-p", help="Inline card list (newline-separated)"),
    output: str = typer.Option("outputs/find_in_binders.html", "--output", "-o"),
):
    """Paste or file a card list; show which binder each card is in."""
    from edhparse.services.find_in_binders import run_find_in_binders

    if not paste and names_file is None:
        typer.echo("Provide a names file or --paste", err=True)
        raise typer.Exit(1)
    cfg = _cfg(fresh_cache=ctx.obj.get("fresh_cache", False))
    result = run_find_in_binders(
        names_file=names_file,
        card_list_text=paste,
        stock_csv=stock,
        out_path=output,
        cfg=cfg,
    )
    _finish(result)


@app.command("safe-shopping")
def cmd_safe_shopping(
    ctx: typer.Context,
    commanders: Path = typer.Option(..., "--commanders", "-cl"),
    output: str = typer.Option("outputs/safe_shopping.html", "--output", "-o"),
    stock: Optional[Path] = typer.Option(None, "--stock"),
    binders: Optional[List[str]] = typer.Option(None, "--binders"),
    min_inclusion: float = typer.Option(20.0, "--min-inclusion"),
    min_synergy: float = typer.Option(20.0, "--min-synergy"),
    min_price: float = typer.Option(0.0, "--min-price"),
    sort_default: str = typer.Option("inclusion", "--sort-default"),
):
    """EDHREC inclusion/synergy shopping HTML."""
    from edhparse.services.safe_shopping import run_safe_shopping

    if stock and not binders:
        typer.echo("--stock requires --binders", err=True)
        raise typer.Exit(1)
    cfg = _cfg(
        fresh_cache=ctx.obj.get("fresh_cache", False),
        safe_shopping_min_inclusion_pct=min_inclusion,
        safe_shopping_min_synergy_pct=min_synergy,
        safe_shopping_min_price=min_price,
        safe_shopping_sort_default=sort_default,
    )
    result = run_safe_shopping(
        commanders,
        output,
        cfg,
        stock_csv=str(stock) if stock else None,
        binders=binders,
    )
    _finish(result)


@app.command("new-cards")
def cmd_new_cards(
    ctx: typer.Context,
    commanders: Path = typer.Option(..., "--commanders", "-cl"),
    output: str = typer.Option("outputs/new_cards.html", "--output", "-o"),
    max_themes: int = typer.Option(5, "--max-themes"),
):
    """Recent-set EDHREC recommendations HTML."""
    from edhparse.services.new_cards import run_new_cards_html

    cfg = _cfg(fresh_cache=ctx.obj.get("fresh_cache", False), max_themes=max_themes)
    result = run_new_cards_html(commanders, output, cfg)
    _finish(result)


@app.command("pivot")
def cmd_pivot(
    ctx: typer.Context,
    decklist: Path = typer.Option(..., "--list", "-l"),
    output: str = typer.Option("outputs/pivot.html", "--output", "-o"),
    min_price: float = typer.Option(5.0, "--min-price"),
    min_inclusion: float = typer.Option(0.0, "--min-inclusion"),
    top: int = typer.Option(40, "--top"),
):
    """Suggest alternate commanders from expensive staples in a deck."""
    from edhparse.services.pivot import run_pivot_html

    cfg = _cfg(
        fresh_cache=ctx.obj.get("fresh_cache", False),
        pivot_min_price=min_price,
        pivot_min_inclusion_pct=min_inclusion,
        pivot_top=top,
    )
    result = run_pivot_html(decklist, output, cfg)
    _finish(result)


@app.command("artist-signings")
def cmd_artist_signings(
    ctx: typer.Context,
    stock: Path = typer.Option(..., "--stock"),
    artists: Path = typer.Option(..., "--artists"),
    binders: List[str] = typer.Option(..., "--binders"),
    output: str = typer.Option("outputs/artist_signings.html", "--output", "-o"),
):
    """Match stock printings to convention artists."""
    from edhparse.services.artist_signings import run_artist_signings

    cfg = _cfg(fresh_cache=ctx.obj.get("fresh_cache", False))
    result = run_artist_signings(stock, binders, artists, cfg, out_path=output)
    _finish(result)


@app.command("shopping-list")
def cmd_shopping_list(
    ctx: typer.Context,
    shopping_list: Path = typer.Argument(..., help="MTGO-format shopping list file"),
    stock: Path = typer.Option(Path("inputs/Stock.csv"), "--stock", "-mb"),
    binders: Optional[List[str]] = typer.Option(
        None,
        "--binders",
        help="Binders counting as owned (default: stock, binder, secret binder)",
    ),
    output: str = typer.Option("outputs/shopping_list.html", "--output", "-o"),
):
    """Merge a shopping list, subtract stock, write owned/buy summary + ManaPool paste txt."""
    from edhparse.services.shopping_list import run_shopping_list

    cfg = _cfg(
        fresh_cache=ctx.obj.get("fresh_cache", False),
        binders=binders,
    )
    result = run_shopping_list(
        shopping_list,
        stock,
        cfg,
        binders=binders,
        out_path=output,
    )
    _finish(result)


@app.command("compare")
def cmd_compare(
    ctx: typer.Context,
    url_a: str = typer.Argument(...),
    url_b: str = typer.Argument(...),
    min_inclusion: Optional[float] = typer.Option(None, "--min-inclusion"),
):
    """Compare overlap of two EDHREC commander pages."""
    from edhparse.services.compare import run_compare

    cfg = _cfg(
        fresh_cache=ctx.obj.get("fresh_cache", False),
        compare_min_inclusion_pct=min_inclusion,
    )
    result = run_compare(url_a, url_b, cfg)
    _finish(result)


@app.command("evaluate")
def cmd_evaluate(
    ctx: typer.Context,
    deck: Path = typer.Argument(..., help="Decklist (Moxfield / Archidekt / MTGO / Arena text export)."),
    commander: Optional[str] = typer.Option(
        None, "--commander", "-c", help="Commander name; separate partners with ';'. Defaults to the deck's Commander section."
    ),
    max_add_budget: float = typer.Option(25.0, "--max-add-budget", help="Max USD per suggested add."),
    tuples: int = typer.Option(8, "--tuples", help="Anchor-tuple cohorts (filtered EDHREC pages) for S1."),
    hubs: int = typer.Option(5, "--hubs", help="Engine hub card pages for S3."),
    max_requests: int = typer.Option(22, "--max-requests", help="Hard cap on uncached EDHREC requests."),
    cache_ttl_days: float = typer.Option(7.0, "--cache-ttl-days", help="Re-fetch EDHREC pages older than this."),
    html: Optional[Path] = typer.Option(None, "--html", help="HTML report path (default outputs/evaluate_<commander>.html)."),
    export_md: Optional[Path] = typer.Option(None, "--export-md", help="Also write a Markdown report."),
    export_csv: Optional[Path] = typer.Option(None, "--export-csv", help="Also write every score to CSV."),
):
    """Score every card (S1-S5) against EDHREC and suggest cuts, adds and 1-for-1 swaps."""
    from edhparse.services.evaluate import run_evaluate

    result = run_evaluate(
        str(deck),
        commander=commander,
        max_add_budget=max_add_budget,
        tuples=tuples,
        hubs=hubs,
        max_requests=max_requests,
        cache_ttl_days=cache_ttl_days,
        fresh_cache=ctx.obj.get("fresh_cache", False),
        html_path=str(html) if html else "",
        md_path=str(export_md) if export_md else None,
        csv_path=str(export_csv) if export_csv else None,
    )
    _finish(result)


@wipcoms_app.command("fetch")
def cmd_wipcoms_fetch(
    ctx: typer.Context,
    input_file: Path = typer.Option(Path("inputs/wantcoms"), "--input", "-i"),
    output_dir: Path = typer.Option(Path("wipcoms"), "--output-dir", "-o"),
    max_themes: int = typer.Option(5, "--max-themes"),
    min_inclusion: float = typer.Option(5.0, "--min-inclusion"),
    min_land_synergy: float = typer.Option(50.0, "--min-land-synergy"),
    min_land_inclusion: float = typer.Option(30.0, "--min-land-inclusion"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
):
    """Fetch EDHREC card dumps for commanders into wipcoms/."""
    from edhparse.services.wipcoms import fetch_wipcoms

    cfg = _cfg(fresh_cache=ctx.obj.get("fresh_cache", False), max_themes=max_themes)
    result = fetch_wipcoms(
        input_file,
        output_dir,
        cfg,
        max_themes=max_themes,
        min_inclusion=min_inclusion,
        min_land_synergy=min_land_synergy,
        min_land_inclusion=min_land_inclusion,
        quiet=quiet,
    )
    _finish(result)


@wipcoms_app.command("singleton")
def cmd_wipcoms_singleton(
    input_path: Path = typer.Argument(..., help="Decklist file"),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
    commander: Optional[str] = typer.Option(None, "--commander"),
    commander_last: bool = typer.Option(False, "--commander-last"),
    include_sideboard: bool = typer.Option(False, "--include-sideboard"),
    in_place: bool = typer.Option(False, "--in-place"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
):
    """Deduplicate a decklist to singleton copies."""
    from edhparse.services.singleton import run_singleton

    result = run_singleton(
        input_path,
        output,
        commander=commander,
        commander_last=commander_last,
        include_sideboard=include_sideboard,
        in_place=in_place,
        quiet=quiet,
    )
    _finish(result)


@app.command("web")
def cmd_web(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
):
    """Launch the local FastAPI UI."""
    from edhparse.web.app import run_server

    run_server(host=host, port=port, open_browser=open_browser)


@app.command("legacy")
def cmd_legacy(ctx: typer.Context):
    """Run the legacy flat argparse CLI (pass remaining args after --)."""
    from edhparse.cli import main as legacy_main

    # Re-invoke with sys.argv stripped of 'legacy'
    # Users: edhparse legacy -- -cl inputs/comlist -html
    typer.echo("Tip: prefer named subcommands. Forwarding to legacy argparse…")
    # Keep argv as-is after 'legacy'; leftover = []
    if "--" in sys.argv:
        idx = sys.argv.index("--")
        leftover = sys.argv[idx + 1 :]
    sys.argv = [sys.argv[0]] + leftover
    legacy_main()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    app()


if __name__ == "__main__":
    main()
