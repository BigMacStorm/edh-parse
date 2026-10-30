"""Local FastAPI UI for edh-parse workflows."""

from __future__ import annotations

import io
import traceback
import webbrowser
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from typing import Callable, Dict, List, Optional

import uvicorn
import json

from fastapi import Body, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core.config import DEFAULT_SUMMARY_BINDERS, RunConfig
from core.stock import StockCollection
from edhparse.web import jobs as job_store

ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = ROOT.parent.parent  # repo root when installed editable
WEB_DIR = ROOT
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))
# Pick up template edits without restarting during local use.
TEMPLATES.env.auto_reload = True
TEMPLATES.env.cache = None

app = FastAPI(title="edh-parse", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

# Serve generated reports
_outputs = PACKAGE_ROOT / "outputs"
_outputs.mkdir(parents=True, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=str(_outputs)), name="outputs")


WORKFLOWS = [
    {"id": "summary", "title": "Collection summary", "blurb": "EDHREC averages + ownership → HTML/CSV."},
    {"id": "shopping-list", "title": "Shopping list", "blurb": "Owned vs still-to-buy, plus a ManaPool paste list."},
    {"id": "convert", "title": "Convert deck", "blurb": "Old → new list with stock coverage."},
    {"id": "diff", "title": "Diff decks", "blurb": "Batch *_old / *_new pairs."},
    {"id": "safe-shopping", "title": "Safe shopping", "blurb": "High inclusion/synergy buys."},
    {"id": "new-cards", "title": "New cards", "blurb": "Recent-set EDHREC recommendations."},
    {"id": "pivot", "title": "Pivot", "blurb": "Expensive staples → alternate commanders."},
    {"id": "artist-signings", "title": "Artist signings", "blurb": "Convention artist match from stock."},
    {"id": "compare", "title": "Compare commanders", "blurb": "EDHREC page overlap."},
    {"id": "evaluate", "title": "Evaluate deck", "blurb": "Five-score EDHREC analysis → cuts, adds, 1-for-1 swaps."},
    {"id": "locate", "title": "Locate cards", "blurb": "Find names in binders."},
    {"id": "find-in-binders", "title": "Find in binders", "blurb": "Paste a card list → binder locations."},
    {"id": "wipcoms", "title": "WIP pipeline", "blurb": "Fetch EDHREC dumps + singletonize."},
    {"id": "challenge", "title": "32 Challenge", "blurb": "One commander per color identity → shareable infographic."},
]


def _flag(value: Optional[str]) -> bool:
    """HTML checkbox → bool (missing/unchecked = False)."""
    if value is None:
        return False
    return str(value).lower() in ("1", "true", "on", "yes")


def _repo_path(rel: str) -> str:
    p = Path(rel)
    if p.is_absolute():
        return str(p)
    return str((PACKAGE_ROOT / p).resolve())


def _run_capturing(fn) -> dict:
    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            result = fn()
    except Exception as e:
        result = {
            "ok": False,
            "message": str(e),
            "error": str(e),
            "outputs": [],
            "traceback": traceback.format_exc(),
        }
    log = buf.getvalue()
    if isinstance(result, dict):
        result = dict(result)
        result["log"] = log
        return result
    return {"ok": True, "message": str(result), "outputs": [], "log": log}


def _page(request: Request, template: str, **ctx):
    return TEMPLATES.TemplateResponse(request, template, ctx)


def _render_result_html(title: str, result: dict) -> str:
    return TEMPLATES.env.get_template("result.html").render(
        request=None,
        title=title,
        result=result,
    )


def _wants_async_job(request: Request) -> bool:
    return request.headers.get("x-async-job", "").lower() in ("1", "true", "yes")


def _dispatch_run(request: Request, title: str, work: Callable[[], dict]):
    """Sync HTML response, or start a background job when the UI asks for progress."""
    if _wants_async_job(request):
        job_id = job_store.start_job(
            title,
            work,
            render_html=lambda result: _render_result_html(title, result),
        )
        return JSONResponse({"job_id": job_id, "title": title})
    result = _run_capturing(work)
    return _page(request, "result.html", title=title, result=result)


@app.get("/api/jobs/{job_id}")
async def api_job_status(job_id: str):
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    return JSONResponse(job.snapshot())



@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return _page(request, "home.html", workflows=WORKFLOWS)


@app.get("/binders")
async def list_binders(stock: str = "inputs/Stock.csv"):
    path = _repo_path(stock)
    names = StockCollection.list_binder_names(path)
    return {"binders": names, "path": path}


CHALLENGE_DIR = PACKAGE_ROOT / "outputs" / "challenge"


@app.get("/workflow/challenge", response_class=HTMLResponse)
async def challenge_page(request: Request):
    from edhparse.services import challenge as ch

    meta = next(w for w in WORKFLOWS if w["id"] == "challenge")
    default_list = "inputs/challenge32"
    return _page(
        request,
        "workflows/challenge.html",
        meta=meta,
        groups=ch.identity_groups(),
        row_labels=ch.ROW_LABELS,
        default_list=default_list,
    )


@app.get("/api/challenge/load")
def api_challenge_load(path: str = "inputs/challenge32", fresh_cache: bool = False):
    """Resolve a commander list file (EDHREC URLs or names, one per line)."""
    from edhparse.services import challenge as ch

    file_path = Path(_repo_path(path))
    if not file_path.is_file():
        return JSONResponse({"error": f"File not found: {path}"}, status_code=404)
    try:
        return ch.build_entries(ch.slugs_from_file(file_path), fresh_cache=fresh_cache)
    except Exception as e:  # surface the reason in the page instead of a bare 500
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)


@app.post("/api/challenge/resolve")
def api_challenge_resolve(payload: dict = Body(...)):
    """Resolve pasted commander URLs/names (one per line)."""
    from edhparse.services import challenge as ch

    slugs = ch.slugs_from_text(str(payload.get("text") or ""))
    if not slugs:
        return JSONResponse({"error": "No commanders found in that text."}, status_code=400)
    try:
        return ch.build_entries(slugs)
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)


@app.get("/api/challenge/prints")
def api_challenge_prints(oracle_id: str):
    """Every artwork for a card, for the art picker."""
    from edhparse.services import challenge as ch

    try:
        return {"prints": ch.fetch_prints(oracle_id)}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=502)


@app.get("/api/img")
def api_img(u: str):
    """Same-origin proxy + disk cache for Scryfall images (keeps PNG export untainted)."""
    from edhparse.services import challenge as ch

    if not ch.is_allowed_image_url(u):
        return JSONResponse({"error": "image host not allowed"}, status_code=400)
    try:
        data, content_type = ch.fetch_image(u)
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=502)
    return Response(data, media_type=content_type, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.post("/api/challenge/save")
def api_challenge_save(payload: dict = Body(...)):
    """Save the editor state as outputs/challenge/<name>.json."""
    from edhparse.services import challenge as ch

    name = ch.safe_name(str(payload.get("name") or "challenge"))
    state = payload.get("state")
    if not isinstance(state, dict):
        return JSONResponse({"error": "missing state"}, status_code=400)
    CHALLENGE_DIR.mkdir(parents=True, exist_ok=True)
    target = CHALLENGE_DIR / f"{name}.json"
    target.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"name": name, "path": f"outputs/challenge/{name}.json"}


@app.get("/api/challenge/saves")
def api_challenge_saves():
    CHALLENGE_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(CHALLENGE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {"saves": [p.stem for p in files]}


@app.get("/api/challenge/open")
def api_challenge_open(name: str):
    from edhparse.services import challenge as ch

    target = CHALLENGE_DIR / f"{ch.safe_name(name)}.json"
    if not target.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"state": json.loads(target.read_text(encoding="utf-8"))}


@app.post("/api/challenge/export")
async def api_challenge_export(request: Request, name: str = "challenge"):
    """Store the PNG the browser rendered, so it also lands in outputs/challenge/."""
    from edhparse.services import challenge as ch

    body = await request.body()
    if not body.startswith(b"\x89PNG\r\n\x1a\n"):
        return JSONResponse({"error": "not a PNG"}, status_code=400)
    CHALLENGE_DIR.mkdir(parents=True, exist_ok=True)
    safe = ch.safe_name(name)
    (CHALLENGE_DIR / f"{safe}.png").write_bytes(body)
    return {"path": f"outputs/challenge/{safe}.png", "url": f"/outputs/challenge/{safe}.png"}


@app.get("/workflow/{workflow_id}", response_class=HTMLResponse)
async def workflow_form(request: Request, workflow_id: str):
    if workflow_id not in {w["id"] for w in WORKFLOWS}:
        return RedirectResponse("/", status_code=302)
    meta = next(w for w in WORKFLOWS if w["id"] == workflow_id)
    stock_default = "inputs/Stock.csv"
    binders = StockCollection.list_binder_names(_repo_path(stock_default))
    return _page(
        request,
        f"workflows/{workflow_id}.html",
        meta=meta,
        defaults={
            "stock": stock_default,
            "commanders": "inputs/comlist" if Path(_repo_path("inputs/comlist")).is_file() else "inputs/wantcoms",
            "wantcoms": "inputs/wantcoms",
            "artists": "inputs/artists",
            "binders": binders or list(DEFAULT_SUMMARY_BINDERS),
            "default_binders": list(DEFAULT_SUMMARY_BINDERS),
        },
    )


@app.post("/run/summary", response_class=HTMLResponse)
async def run_summary_route(
    request: Request,
    commander: str = Form(""),
    commanders: str = Form(""),
    decklist: str = Form(""),
    stock: str = Form("inputs/Stock.csv"),
    write_html: Optional[str] = Form(None),
    write_csv: Optional[str] = Form(None),
    latest_set: Optional[str] = Form(None),
    fresh_cache: Optional[str] = Form(None),
    show_owned: Optional[str] = Form(None),
    need: Optional[str] = Form(None),
):
    from edhparse.services.summary import run_summary

    cfg = RunConfig(
        fresh_cache=_flag(fresh_cache),
        show_owned=_flag(show_owned),
        need=_flag(need),
    )

    def work():
        return run_summary(
            cfg,
            commander=commander.strip() or None,
            commander_list=_repo_path(commanders) if commanders.strip() else None,
            card_list=_repo_path(decklist) if decklist.strip() else None,
            manabox=_repo_path(stock) if stock.strip() else None,
            write_csv=_flag(write_csv),
            html_path="outputs/summary.html" if _flag(write_html) else None,
            latest_set_html="outputs/latest_set.html" if _flag(latest_set) else None,
        )

    return _dispatch_run(request, "Collection summary", work)


@app.post("/run/convert", response_class=HTMLResponse)
async def run_convert_route(
    request: Request,
    old_deck: str = Form(...),
    new_deck: str = Form(...),
    stock: str = Form(...),
    binders: str = Form("Stock,Binder"),
    sort_by: str = Form("price"),
    fresh_cache: bool = Form(False),
):
    from edhparse.services.convert import run_convert_deck

    binder_list = [b.strip() for b in binders.replace(";", ",").split(",") if b.strip()]
    cfg = RunConfig(fresh_cache=fresh_cache, convert_sort=sort_by)

    def work():
        return run_convert_deck(
            _repo_path(old_deck),
            _repo_path(new_deck),
            _repo_path(stock),
            binder_list,
            cfg,
        )

    return _dispatch_run(request, "Convert deck", work)


@app.post("/run/diff", response_class=HTMLResponse)
async def run_diff_route(
    request: Request,
    directory: str = Form(...),
    stock: str = Form(...),
    fresh_cache: bool = Form(False),
):
    from edhparse.services.diff import run_diff_directory

    cfg = RunConfig(fresh_cache=fresh_cache)

    def work():
        return run_diff_directory(_repo_path(directory), _repo_path(stock), cfg)

    return _dispatch_run(request, "Diff decks", work)


@app.post("/run/safe-shopping", response_class=HTMLResponse)
async def run_safe_shopping_route(
    request: Request,
    commanders: str = Form(...),
    output: str = Form("outputs/safe_shopping.html"),
    stock: str = Form(""),
    binders: str = Form(""),
    min_inclusion: float = Form(20.0),
    min_synergy: float = Form(20.0),
    min_price: float = Form(0.0),
    fresh_cache: bool = Form(False),
):
    from edhparse.services.safe_shopping import run_safe_shopping

    binder_list = [b.strip() for b in binders.replace(";", ",").split(",") if b.strip()] or None
    cfg = RunConfig(
        fresh_cache=fresh_cache,
        safe_shopping_min_inclusion_pct=min_inclusion,
        safe_shopping_min_synergy_pct=min_synergy,
        safe_shopping_min_price=min_price,
    )

    def work():
        return run_safe_shopping(
            _repo_path(commanders),
            output if Path(output).is_absolute() else str(PACKAGE_ROOT / output),
            cfg,
            stock_csv=_repo_path(stock) if stock.strip() else None,
            binders=binder_list,
        )

    return _dispatch_run(request, "Safe shopping", work)


@app.post("/run/new-cards", response_class=HTMLResponse)
async def run_new_cards_route(
    request: Request,
    commanders: str = Form(...),
    output: str = Form("outputs/new_cards.html"),
    max_themes: int = Form(5),
    fresh_cache: Optional[str] = Form(None),
):
    from edhparse.services.new_cards import run_new_cards_html

    cfg = RunConfig(fresh_cache=_flag(fresh_cache), max_themes=max_themes)
    out = output if Path(output).is_absolute() else str(PACKAGE_ROOT / output)

    def work():
        return run_new_cards_html(_repo_path(commanders), out, cfg)

    return _dispatch_run(request, "New cards", work)


@app.post("/run/pivot", response_class=HTMLResponse)
async def run_pivot_route(
    request: Request,
    decklist: str = Form(...),
    output: str = Form("outputs/pivot.html"),
    min_price: float = Form(5.0),
    min_inclusion: float = Form(0.0),
    top: int = Form(40),
    fresh_cache: bool = Form(False),
):
    from edhparse.services.pivot import run_pivot_html

    cfg = RunConfig(
        fresh_cache=fresh_cache,
        pivot_min_price=min_price,
        pivot_min_inclusion_pct=min_inclusion,
        pivot_top=top,
    )
    out = output if Path(output).is_absolute() else str(PACKAGE_ROOT / output)

    def work():
        return run_pivot_html(_repo_path(decklist), out, cfg)

    return _dispatch_run(request, "Pivot", work)


@app.post("/run/artist-signings", response_class=HTMLResponse)
async def run_artist_route(
    request: Request,
    stock: str = Form(...),
    artists: str = Form(...),
    binders: str = Form(...),
    output: str = Form("outputs/artist_signings.html"),
    fresh_cache: bool = Form(False),
):
    from edhparse.services.artist_signings import run_artist_signings

    binder_list = [b.strip() for b in binders.replace(";", ",").split(",") if b.strip()]
    cfg = RunConfig(fresh_cache=fresh_cache)
    out = output if Path(output).is_absolute() else str(PACKAGE_ROOT / output)

    def work():
        return run_artist_signings(
            _repo_path(stock), binder_list, _repo_path(artists), cfg, out_path=out
        )

    return _dispatch_run(request, "Artist signings", work)


@app.post("/run/compare", response_class=HTMLResponse)
async def run_compare_route(
    request: Request,
    url_a: str = Form(...),
    url_b: str = Form(...),
    min_inclusion: Optional[float] = Form(None),
    fresh_cache: bool = Form(False),
):
    from edhparse.services.compare import run_compare

    cfg = RunConfig(fresh_cache=fresh_cache, compare_min_inclusion_pct=min_inclusion)

    def work():
        return run_compare(url_a, url_b, cfg)

    return _dispatch_run(request, "Compare commanders", work)


@app.post("/run/evaluate", response_class=HTMLResponse)
async def run_evaluate_route(
    request: Request,
    decklist: str = Form(""),
    deck_text: str = Form(""),
    commander: str = Form(""),
    max_add_budget: Optional[str] = Form("25.0"),
    tuples: int = Form(8),
    hubs: int = Form(5),
    max_requests: int = Form(22),
    cache_ttl_days: float = Form(7.0),
    write_md: Optional[str] = Form(None),
    write_csv: Optional[str] = Form(None),
    fresh_cache: Optional[str] = Form(None),
):
    from edhparse.services.evaluate import run_evaluate

    def work():
        try:
            budget_val = float(max_add_budget.strip()) if max_add_budget and max_add_budget.strip() else 0.0
        except (ValueError, TypeError):
            budget_val = 0.0
        return run_evaluate(
            _repo_path(decklist) if decklist.strip() else None,
            deck_text=deck_text or None,
            commander=commander.strip() or None,
            max_add_budget=budget_val,
            tuples=tuples,
            hubs=hubs,
            max_requests=max_requests,
            cache_ttl_days=cache_ttl_days,
            fresh_cache=_flag(fresh_cache),
            write_md=_flag(write_md),
            write_csv=_flag(write_csv),
            out_dir=str(_outputs),
        )

    return _dispatch_run(request, "Evaluate deck", work)


@app.post("/run/locate", response_class=HTMLResponse)
async def run_locate_route(
    request: Request,
    names_file: str = Form(...),
    stock: str = Form(...),
    fresh_cache: bool = Form(False),
):
    from edhparse.services.locate import run_locate

    cfg = RunConfig(fresh_cache=fresh_cache)

    def work():
        return run_locate(_repo_path(names_file), _repo_path(stock), cfg)

    return _dispatch_run(request, "Locate cards", work)


@app.post("/run/find-in-binders", response_class=HTMLResponse)
async def run_find_in_binders_route(
    request: Request,
    card_list: str = Form(...),
    stock: str = Form(...),
    output: str = Form("outputs/find_in_binders.html"),
):
    from edhparse.services.find_in_binders import run_find_in_binders

    def work():
        out = output if Path(output).is_absolute() else str(PACKAGE_ROOT / output)
        return run_find_in_binders(
            card_list_text=card_list,
            stock_csv=_repo_path(stock),
            out_path=out,
        )

    return _dispatch_run(request, "Find in binders", work)


@app.post("/run/wipcoms", response_class=HTMLResponse)
async def run_wipcoms_route(
    request: Request,
    input_file: str = Form("inputs/wantcoms"),
    output_dir: str = Form("wipcoms"),
    max_themes: int = Form(5),
    min_inclusion: float = Form(5.0),
    singleton_after: bool = Form(False),
    fresh_cache: bool = Form(False),
):
    from edhparse.services.wipcoms import fetch_wipcoms
    from edhparse.services.singleton import run_singleton

    cfg = RunConfig(fresh_cache=fresh_cache, max_themes=max_themes)
    do_singleton = bool(singleton_after)

    def work():
        result = fetch_wipcoms(
            _repo_path(input_file),
            _repo_path(output_dir),
            cfg,
            max_themes=max_themes,
            min_inclusion=min_inclusion,
        )
        if do_singleton and result.get("ok"):
            out_dir = Path(_repo_path(output_dir))
            for path in out_dir.iterdir():
                if path.is_file() and not path.name.endswith(".bak"):
                    run_singleton(path, in_place=True, commander_last=True, quiet=True)
            result = dict(result)
            result["message"] = (result.get("message") or "") + " (singletonized)"
        return result

    return _dispatch_run(request, "WIP pipeline", work)


@app.post("/run/shopping-list", response_class=HTMLResponse)
async def run_shopping_list_route(
    request: Request,
    shopping_list: str = Form(...),
    stock: str = Form("inputs/Stock.csv"),
    binders: str = Form("Stock, Binder, secret binder"),
    output: str = Form("outputs/shopping_list.html"),
    fresh_cache: Optional[str] = Form(None),
):
    from edhparse.services.shopping_list import run_shopping_list

    binder_list = [b.strip() for b in binders.replace(";", ",").split(",") if b.strip()]
    cfg = RunConfig(fresh_cache=_flag(fresh_cache), binders=binder_list)
    out = output if Path(output).is_absolute() else str(PACKAGE_ROOT / output)

    def work():
        return run_shopping_list(
            _repo_path(shopping_list),
            _repo_path(stock),
            cfg,
            binders=binder_list,
            out_path=out,
        )

    return _dispatch_run(request, "Shopping list", work)


def run_server(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True):
    url = f"http://{host}:{port}"
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=host, port=port, log_level="info")


def main():
    run_server()


if __name__ == "__main__":
    main()
