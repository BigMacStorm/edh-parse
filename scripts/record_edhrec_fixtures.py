"""Record EDHREC pages and frozen card metadata for the real-data backtest (tests/real_backtest.py).

One-off, network-using dev tool. It plays the backtest's own trial decks through the real
``Evaluator`` against a recording session, so exactly the pages the evaluator asks for - tuple
filter pages, theme pages, hub pages, 403s and all - are captured, trimmed and written to
``tests/fixtures/real/<slug>/``. The card metadata (Scryfall data, ManaPool prices, role tags) seen
along the way is frozen into ``tests/fixtures/real/card_meta_snapshot.json``.

All EDHREC traffic goes through ``EdhrecFetcher`` (cache-first, 1.8-3.2 s jitter between requests).

    python scripts/record_edhrec_fixtures.py [--out tests/fixtures/real] [--seeds 1 2 3] [--themes 2]
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clients.session import create_cached_session, ensure_scryfall_bulk  # noqa: E402
from edhparse.evaluate import config as C  # noqa: E402
from edhparse.evaluate.edhrec import (  # noqa: E402
    EdhrecFetcher, Telemetry, commander_url, parse_filtered_html, parse_json_page,
)
from edhparse.evaluate.engine import Evaluator  # noqa: E402
from edhparse.evaluate.deck_parser import parse_decklist_text  # noqa: E402
from edhparse.evaluate.models import name_key  # noqa: E402
from edhparse.evaluate.replay import (  # noqa: E402
    FixtureWriter, RecordingSession, SnapshotMetaLookup, snapshot_from_metas,
)
from edhparse.evaluate.scryfall_meta import CardMetaLookup, RoleTagger, manapool_price_lookup  # noqa: E402
from tests.real_backtest import (  # noqa: E402
    COMMANDERS, PRICE_TIER_THEMES, SNAPSHOT, CommanderInputs, _slug, build_trial, scoring_profile,
)

# Profiles that change which pages the evaluator requests (hub selection follows the S1 weights).
RECORD_PROFILES = ("current", "sqrt_weight")


def tag_snapshot(session, snapshot, attempts: int = 8) -> bool:
    """Role-tag EVERY card in the snapshot (not just the ones the evaluator tagged on the recording run), so a
    code change or ablation that promotes a different candidate into the finalists never sees empty roles on
    replay. Works on copies and commits only on full success: ``RoleTagger`` abandons the whole pass on one
    failed Scryfall query and then overwrites roles with regex guesses. Its progress is cached, so a retry
    resumes where the last attempt stopped."""
    for attempt in range(1, attempts + 1):
        lookup = SnapshotMetaLookup(snapshot)
        cards = [lookup.get(d["name"]) for d in snapshot.values()]
        tel = Telemetry()
        print(f"role-tagging {len(cards)} snapshot cards, attempt {attempt} (cached tags are reused)...")
        tagger = RoleTagger(session, tel)
        tagger.tag(cards)
        if not tagger.failed:
            for card in cards:
                entry = snapshot[name_key(card.name)]
                entry["roles"] = sorted(card.roles)
                entry["tagged"] = True
            return True
        print(f"   attempt {attempt} failed: {(tel.log or ['?'])[0]}; retrying in {10 * attempt}s")
        time.sleep(10 * attempt)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ROOT / "tests" / "fixtures" / "real"))
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--themes", type=int, default=2)
    ap.add_argument("--retag-only", action="store_true", help="only (re)tag the existing snapshot; no EDHREC traffic")
    args = ap.parse_args()
    out = Path(args.out)

    if args.retag_only:
        path = out / SNAPSHOT
        snapshot = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        ok = tag_snapshot(create_cached_session(), snapshot)
        if ok:
            path.write_bytes(gzip.compress(json.dumps(snapshot, separators=(",", ":"), sort_keys=True).encode("utf-8"), mtime=0))
        print(f"snapshot {'tagged' if ok else 'NOT updated'}: {len(snapshot)} cards")
        return 0 if ok else 1

    session = create_cached_session()
    if not ensure_scryfall_bulk(session):
        print("Scryfall bulk data unavailable; cannot resolve card metadata.")
        return 1
    metas = CardMetaLookup(price_lookup=manapool_price_lookup(session))

    # ---- phase A: baseline + theme pages for every commander (needed to build trials) -------------
    rigs = {}
    for name in COMMANDERS:
        slug = _slug(name)
        writer = FixtureWriter(out / slug, meta={"commander": name, "slug": slug})
        rec = RecordingSession(session, writer, max_age_seconds=C.DEFAULT_CACHE_TTL_DAYS * 86400)
        fetcher = EdhrecFetcher(rec, max_requests=500, missing_cache_path=None)
        data = fetcher.fetch(commander_url(slug), "json")
        if not isinstance(data, dict):
            print(f"!! no EDHREC commander page for {name} ({slug}); aborting")
            return 1
        d0 = parse_json_page(data, f"D0 {name}")
        print(f"{name}: N={d0.num_decks}, {len(d0.cards)} cards listed")
        links = sorted((d0.raw or {}).get("panels", {}).get("taglinks") or [], key=lambda t: -(t.get("count") or 0))
        themes = {}
        for link in links:
            ts = link.get("slug")
            if not ts or ts in PRICE_TIER_THEMES:
                continue
            page = fetcher.fetch(commander_url(slug, ts), "json")
            if isinstance(page, dict):
                themes[ts] = parse_json_page(page, f"{name} / {ts}")
                print(f"   theme {ts}: N={themes[ts].num_decks}")
            if len(themes) >= args.themes:
                break
        rigs[slug] = (name, writer, rec, fetcher, d0, themes)

    # ---- phase B: play every trial deck through the real evaluator, recording what it requests ------
    inputs = {}
    for slug, (name, _w, _r, _f, d0, themes) in rigs.items():
        inputs[slug] = CommanderInputs(
            slug=slug, name=name, color_identity=tuple(metas.get(name).color_identity), d0=d0, themes=themes,
            others={s: r[4] for s, r in rigs.items() if s != slug})
    for slug, (name, writer, rec, fetcher, _d0, themes) in rigs.items():
        ev = Evaluator(rec, C.EvaluateOptions(max_requests=500), fetcher=fetcher, metas=metas,
                       log=lambda m: None)
        for theme in themes:
            for seed in args.seeds:
                trial = build_trial(inputs[slug], theme, seed, metas)
                before = fetcher.telemetry.edhrec_requests
                # Hub choice depends on the S1 cohort weights, so play the trial under every weighting we
                # compare; otherwise an ablation would ask for hub pages that were never recorded.
                for profile in RECORD_PROFILES:
                    with scoring_profile(profile):
                        res = ev.run(parse_decklist_text(trial.decklist_text(name)))
                print(f"{name} / {theme} / seed {seed}: {fetcher.telemetry.edhrec_requests - before} new requests; "
                      f"{res.telemetry.filter_mode}; {len(res.tuples)} tuples, {len(res.hubs)} hubs; "
                      f"holdout {len(trial.holdout)}, traps {len(trial.traps)}")
        tel = fetcher.telemetry
        if tel.blocked:
            print(f"!! {name}: blocked hosts {sorted(tel.blocked_hosts)}")
        empty = [u for u, e in writer._entries.items() if "?f=" in u and e["status"] == 200
                 and not parse_filtered_html(e["text"], "x", u, slug).cards]
        if empty:
            print(f"!! {name}: {len(empty)} of {sum('?f=' in u for u in writer._entries)} filter pages hold no cards")
        writer.save()
        print(f"   saved {name}: {sum(f.stat().st_size for f in (out / slug).iterdir()) / 1024:.0f} KB, "
              f"{tel.edhrec_requests} network requests")

    found = [m for m in metas._memo.values() if m.found]
    snapshot = snapshot_from_metas(found, tagged=False)  # roles as the evaluator tagged them on this run
    if not tag_snapshot(session, snapshot):
        print("!! could not role-tag every snapshot card; the snapshot keeps the evaluator's roles with tagged=False "
              "(re-run with --retag-only)")
    (out / SNAPSHOT).write_bytes(gzip.compress(json.dumps(snapshot, separators=(",", ":"), sort_keys=True).encode("utf-8"),
                                               mtime=0))
    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"snapshot: {len(snapshot)} cards. Fixture corpus: {total / 1024:.0f} KB total in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
