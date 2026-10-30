"""Regression gate on the recorded real-data corpus (4 commanders x 2 themes x 3 folds = 24 trials).

Replay is deterministic, so the gate is exact: any change that moves a score must refresh the baseline
on purpose (``EDH_BACKTEST_UPDATE=1 pytest tests/test_real_corpus.py``) and say why in the commit
message. Judge a scoring change with ``python -m tests.real_backtest --ablation`` (cluster-paired
deltas), not by the size of the baseline move.

Skipped when the corpus has not been recorded (``python scripts/record_edhrec_fixtures.py``).
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

from edhparse.evaluate import config as C
from tests.real_backtest import (
    QUALITY, REAL_DIR, SNAPSHOT, build_trial, load_inputs, load_snapshot, run_corpus, run_trial, summarize,
)
from edhparse.evaluate.replay import SnapshotMetaLookup

BASELINE = Path(__file__).parent / "baselines" / "real_backtest.json"
N_TRIALS = 24
TOL = 1.5e-3  # baseline values are stored rounded to 4 decimals

pytestmark = pytest.mark.skipif(not (REAL_DIR / SNAPSHOT).exists(), reason="real fixture corpus not recorded")


@pytest.fixture(scope="module")
def rows():
    return run_corpus()


def _clean(summary):
    return {who: {k: (None if isinstance(v, float) and math.isnan(v) else round(v, 4)) for k, v in vals.items()}
            for who, vals in summary.items()}


def test_every_trial_replays_completely_on_tier_a(rows):
    assert len(rows) == N_TRIALS
    assert [r["commander"] + r["theme"] for r in rows if r["unrecorded_urls"]] == [], "a trial asked for an unrecorded page"
    assert all(r["tier_a"] == 1.0 for r in rows), "a recorded trial lost its live filter pages (Tier A)"
    assert all(r["tuples"] == 8 and r["hubs"] == 5 for r in rows)


def test_every_trial_is_a_full_100_card_deck_with_a_full_holdout(rows):
    assert [r["deck_total"] for r in rows] == [100.0] * N_TRIALS
    assert all(r["n_head"] == 3 and r["n_tail"] == 3 for r in rows), "a trial lost part of its holdout"


def test_no_card_is_scored_with_roles_that_were_never_looked_up(rows):
    assert sum(r["untagged_cards"] for r in rows) == 0, "snapshot lacks role tags for a card the evaluator scored"


def test_no_recommended_swap_breaks_a_role_floor(rows):
    assert sum(r["role_violations"] for r in rows) == 0


def test_corpus_run_is_deterministic(rows):
    again = run_corpus()
    assert json.dumps(_clean(summarize(again)), sort_keys=True) == json.dumps(_clean(summarize(rows)), sort_keys=True)
    assert [r["trial_fp"] for r in again] == [r["trial_fp"] for r in rows]


def test_the_production_request_budget_still_leaves_room_for_every_hub():
    """The corpus run uses a 500-request budget; this plays one trial per commander under the real 22."""
    snapshot = load_snapshot()
    inputs = load_inputs(REAL_DIR, snapshot)
    metas = SnapshotMetaLookup(snapshot)
    for slug, ci in inputs.items():
        theme = next(iter(ci.themes))
        res, m = run_trial(REAL_DIR, snapshot, ci, build_trial(ci, theme, 1, metas), max_requests=C.DEFAULT_MAX_REQUESTS)
        assert m["unrecorded_urls"] == 0
        assert res.telemetry.edhrec_requests <= C.DEFAULT_MAX_REQUESTS, slug
        assert len(res.hubs) == C.DEFAULT_HUBS, f"{slug}: the request budget starved the S3 hub pages"
        assert any(t.tier == "A" for t in res.tuples), slug


def test_real_backtest_matches_the_recorded_baseline(rows):
    current = _clean(summarize(rows))
    fingerprints = {f"{r['commander']}|{r['theme']}|{r['seed']}": r["trial_fp"] for r in rows}
    if os.environ.get("EDH_BACKTEST_UPDATE") == "1":
        BASELINE.write_text(json.dumps({**current, "_trials": fingerprints}, indent=1) + "\n")
        pytest.skip("baseline refreshed")
    baseline = json.loads(BASELINE.read_text())
    assert baseline.pop("_trials") == fingerprints, "trial decks changed: the builder or the corpus drifted"
    for who, vals in baseline.items():
        for key in QUALITY:
            if vals.get(key) is None or current[who].get(key) is None:
                assert vals.get(key) is None and current[who].get(key) is None, f"{who} {key}: defined-ness changed"
                continue
            assert abs(current[who][key] - vals[key]) <= TOL, (
                f"{who} {key}: {vals[key]} -> {current[who][key]}; if intended, refresh the baseline and justify it")


def test_decks_get_close_to_ten_swaps_not_three(rows):
    swaps = [r["n_swaps"] for r in rows]
    assert min(swaps) >= 3 and max(swaps) <= C.MAX_SWAPS
    assert sum(swaps) / len(swaps) >= 8.0, "the add-side bar is starving the swap matcher again"
