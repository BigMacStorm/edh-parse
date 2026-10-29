"""Record/replay tooling for the real-data backtest: trimming must not change what the parsers see."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edhparse.evaluate import config as C
from edhparse.evaluate.edhrec import filtered_page_url, parse_filtered_html, parse_json_page
from edhparse.evaluate.models import CardMeta
from edhparse.evaluate.replay import (
    FixtureWriter,
    RecordingSession,
    ReplaySession,
    SnapshotMetaLookup,
    SnapshotTagger,
    canonical_url,
    snapshot_from_metas,
    trim_edhrec_json,
    trim_filtered_html,
)
from tests.test_evaluate import FakeResp, FakeSession, filtered_html

FIXTURES = Path(__file__).parent / "fixtures"
SLUG = "korvold-fae-cursed-king"
D0_URL = f"{C.EDHREC_JSON_BASE}/commanders/{SLUG}.json"


def _stats(cohort):
    return {n: (s.num_decks, s.potential_decks, s.synergy, s.lift, s.category) for n, s in
            ((s.name, s) for s in cohort.cards.values())}


def test_trimmed_json_parses_identically_and_is_smaller():
    raw = json.loads((FIXTURES / "edhrec_commander_korvold.json").read_text(encoding="utf-8"))
    trimmed = trim_edhrec_json(raw)
    a = parse_json_page(raw, "x")
    b = parse_json_page(trimmed, "x")
    assert b.num_decks == a.num_decks > 0
    assert _stats(b) == _stats(a)
    assert b.floor_rate == a.floor_rate and b.top_cards(5) == a.top_cards(5)
    # the structural panels the evaluator reads survive
    assert trimmed["panels"]["mana_curve"] == raw["panels"]["mana_curve"]
    assert [(t["slug"], t["count"]) for t in trimmed["panels"]["taglinks"]] == \
        [(t["slug"], t["count"]) for t in raw["panels"]["taglinks"]]
    for t in ("creature", "instant", "sorcery", "artifact", "enchantment", "planeswalker"):
        assert trimmed.get(t) == raw.get(t)
    assert len(json.dumps(trimmed)) < 0.75 * len(json.dumps(raw))  # this fixture is already a slim page


def test_trim_keeps_redirects_and_average_deck_lists():
    assert trim_edhrec_json({"redirect": "/commanders/a-b", "junk": 1}) == {"redirect": "/commanders/a-b"}
    avg = {"deck": {"cards": {"x": [["Sol Ring", 1]]}, "stats": {"junk": 1}}, "header": "junk"}
    assert trim_edhrec_json(avg) == {"deck": {"cards": {"x": [["Sol Ring", 1]]}}}


def test_trimmed_filtered_html_parses_identically_and_is_smaller():
    html = (FIXTURES / "edhrec_filtered_korvold.html").read_text(encoding="utf-8")
    trimmed = trim_filtered_html(html)
    a = parse_filtered_html(html, "x", commander=SLUG)
    b = parse_filtered_html(trimmed, "x", commander=SLUG)
    assert b.num_decks == a.num_decks > 0
    assert _stats(b) == _stats(a)
    assert len(trimmed) <= len(html)


# --------------------------------------------------------------------------- replay


def _record(tmp_path):
    w = FixtureWriter(tmp_path)
    raw = json.loads((FIXTURES / "edhrec_commander_korvold.json").read_text(encoding="utf-8"))
    w.add(D0_URL, 200, "application/json", json.dumps(raw))
    missing = f"{C.EDHREC_JSON_BASE}/commanders/{SLUG}/budget.json"
    w.add(missing, 403, "application/xml", "<Error><Code>AccessDenied</Code></Error>")
    flt = filtered_page_url(SLUG, ["mayhem-devil", "treasure-nabber"], ["x-card"])
    w.add(flt, 200, "text/html", filtered_html(100, {"Mayhem Devil": (90, 0.3)}, commander=SLUG))
    w.save()
    return D0_URL, missing, flt


def test_replay_serves_recorded_pages_and_missing_markers(tmp_path):
    d0, missing, flt = _record(tmp_path)
    s = ReplaySession(tmp_path)
    r = s.get(d0, timeout=5, headers={})
    assert r.status_code == 200 and parse_json_page(r.json(), "d0").num_decks > 0
    m = s.get(missing)
    assert m.status_code == 403 and "AccessDenied" in m.text and "xml" in m.headers["Content-Type"]
    assert s.get(flt).status_code == 200
    assert s.unrecorded == []


def test_replay_cache_probe_misses_so_the_fetcher_takes_the_replay_path(tmp_path):
    d0, _, _ = _record(tmp_path)
    s = ReplaySession(tmp_path)
    assert s.get(d0, only_if_cached=True).status_code == 504


def test_replay_filter_lookup_is_order_invariant_and_unrecorded_urls_are_tracked(tmp_path):
    _, _, flt = _record(tmp_path)
    s = ReplaySession(tmp_path)
    # the live URL builder now sorts, so reorder by hand to model an older recording / other builder
    swapped = flt.replace("in%3Dmayhem-devil%3Bin%3Dtreasure-nabber", "in%3Dtreasure-nabber%3Bin%3Dmayhem-devil")
    assert swapped != flt and canonical_url(swapped) == canonical_url(flt)
    assert s.get(swapped).status_code == 200
    other = filtered_page_url(SLUG, ["somebody-else", "mayhem-devil"])
    assert s.get(other).status_code == 504
    assert s.unrecorded == [other]


# --------------------------------------------------------------------------- recording


def test_recording_session_records_edhrec_responses_and_ignores_the_rest(tmp_path):
    payload = json.dumps({"container": {"json_dict": {"card": {"num_decks": 5}, "cardlists": []}}})
    cached = FakeResp(200, payload, from_cache=True)
    inner = FakeSession(
        routes={D0_URL: FakeResp(200, payload),
                f"{C.EDHREC_JSON_BASE}/cards/x.json": FakeResp(403, "<Error><Code>AccessDenied</Code></Error>", "application/xml"),
                "https://api.scryfall.com/cards/search?q=otag%3Aramp": FakeResp(200, "{}")},
        cache={f"{C.EDHREC_JSON_BASE}/commanders/cached.json": cached})
    w = FixtureWriter(tmp_path)
    rec = RecordingSession(inner, w)
    rec.get(D0_URL)
    rec.get(f"{C.EDHREC_JSON_BASE}/cards/x.json")
    rec.get("https://api.scryfall.com/cards/search", params={"q": "otag:ramp"})
    rec.get(f"{C.EDHREC_JSON_BASE}/commanders/cached.json", only_if_cached=True)  # served from cache: still recorded
    rec.get(f"{C.EDHREC_JSON_BASE}/commanders/uncached.json", only_if_cached=True)  # 504 miss: not recorded
    w.save()
    s = ReplaySession(tmp_path)
    assert set(s.routes) == {D0_URL, f"{C.EDHREC_JSON_BASE}/cards/x.json", f"{C.EDHREC_JSON_BASE}/commanders/cached.json"}
    assert s.get(f"{C.EDHREC_JSON_BASE}/cards/x.json").status_code == 403


# --------------------------------------------------------------------------- frozen card metadata


def test_snapshot_round_trips_card_metadata_and_roles():
    ramp = CardMeta(name="Sol Ring", front="Sol Ring", cmc=1, type_line="Artifact", price=1.5, price_known=True,
                    roles={"RAMP"}, color_identity=())
    mdfc = CardMeta(name="Valakut Awakening // Valakut Stoneforge", front="Valakut Awakening", cmc=3,
                    type_line="Instant", layout="modal_dfc", is_mdfc_land=True, color_identity=("R",))
    snap = snapshot_from_metas([ramp, mdfc])
    lookup = SnapshotMetaLookup(json.loads(json.dumps(snap)))
    got = lookup.get("sol ring")
    assert got.found and got.price == 1.5 and got.price_known and got.roles == set()  # roles come from the tagger
    assert lookup.get("Valakut Awakening").is_mdfc_land  # front-face lookup
    assert not lookup.get("Nonexistent Card").found
    tagger = SnapshotTagger(json.loads(json.dumps(snap)))
    tagger.tag([got])
    assert got.roles == {"RAMP"} and tagger.failed is False


def test_recording_session_never_records_a_rate_limit_page(tmp_path):
    from tests.test_evaluate import SOFT_429

    web = filtered_page_url(SLUG, ["a", "b"])
    w = FixtureWriter(tmp_path)
    rec = RecordingSession(FakeSession({web: FakeResp(200, SOFT_429, "text/html")}), w)
    rec.get(web)
    w.save()
    assert ReplaySession(tmp_path).routes == {}


def test_fixtures_are_stored_gzipped_and_replay_transparently(tmp_path):
    d0, missing, flt = _record(tmp_path)
    pages = [p.name for p in tmp_path.iterdir() if p.name != "manifest.json"]
    assert pages and all(name.endswith(".gz") for name in pages)
    s = ReplaySession(tmp_path)
    assert parse_json_page(s.get(d0).json(), "d0").num_decks > 0
    assert "AccessDenied" in s.get(missing).text
    assert s.get(flt).status_code == 200


def test_gzipped_fixtures_are_byte_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        _record(d)
    names = sorted(p.name for p in a.iterdir() if p.name != "manifest.json")
    assert names == sorted(p.name for p in b.iterdir() if p.name != "manifest.json")
    assert all((a / n).read_bytes() == (b / n).read_bytes() for n in names)


def test_compress_fixture_dir_converts_a_plain_directory(tmp_path):
    from edhparse.evaluate.replay import compress_fixture_dir

    w = FixtureWriter(tmp_path, compress=False)
    w.add(D0_URL, 200, "application/json",
          json.dumps({"container": {"json_dict": {"card": {"num_decks": 7}, "cardlists": []}}}))
    w.save()
    assert any(p.suffix == ".json" for p in tmp_path.iterdir() if p.name != "manifest.json")
    compress_fixture_dir(tmp_path)
    assert not any(p.suffix == ".json" for p in tmp_path.iterdir() if p.name != "manifest.json")
    assert parse_json_page(ReplaySession(tmp_path).get(D0_URL).json(), "d0").num_decks == 7
    compress_fixture_dir(tmp_path)  # idempotent
    assert ReplaySession(tmp_path).get(D0_URL).status_code == 200


# =========================================================================== phase 4: replay robustness


def test_recording_session_ignores_stale_cache_probes(tmp_path):
    fresh_url = f"{C.EDHREC_JSON_BASE}/commanders/fresh.json"
    stale_url = f"{C.EDHREC_JSON_BASE}/commanders/stale.json"
    payload = json.dumps({"container": {"json_dict": {"card": {"num_decks": 5}, "cardlists": []}}})
    inner = FakeSession(cache={fresh_url: FakeResp(200, payload, from_cache=True, age_days=1),
                               stale_url: FakeResp(200, payload, from_cache=True, age_days=30)})
    w = FixtureWriter(tmp_path)
    rec = RecordingSession(inner, w, max_age_seconds=7 * 86400)
    rec.get(fresh_url, only_if_cached=True)
    rec.get(stale_url, only_if_cached=True)  # the fetcher would refresh this one, so it must not be recorded as-is
    w.save()
    assert set(ReplaySession(tmp_path).routes) == {fresh_url}


def test_snapshot_tagger_reapplies_role_rules_and_reports_untagged_cards():
    big = CardMeta(name="Big Rock", front="Big Rock", cmc=6, type_line="Artifact", roles={"RAMP"})
    small = CardMeta(name="Small Rock", front="Small Rock", cmc=2, type_line="Artifact", roles={"RAMP"})
    snap = snapshot_from_metas([big, small], tagged=True)
    snap["mystery card"] = {**snapshot_from_metas([CardMeta(name="Mystery Card", front="Mystery Card")])["mystery card"],
                            "tagged": False}
    tagger = SnapshotTagger(json.loads(json.dumps(snap)))
    lookup = SnapshotMetaLookup(json.loads(json.dumps(snap)))
    metas = [lookup.get("Big Rock"), lookup.get("Small Rock"), lookup.get("Mystery Card")]
    tagger.tag(metas)
    assert metas[0].roles == set(), "RAMP above RAMP_MAX_CMC is dropped by the rules even if the snapshot says RAMP"
    assert metas[1].roles == {"RAMP"}
    assert tagger.untagged == ["Mystery Card"]
