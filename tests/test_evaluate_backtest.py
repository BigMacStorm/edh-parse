"""Regression gate: scoring changes may not make the synthetic backtest worse.

Refresh the recorded baseline deliberately with ``EDH_BACKTEST_UPDATE=1 pytest tests/test_evaluate_backtest.py``
and explain the change in the commit message.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.evaluate_backtest import mean_reciprocal_rank, recall_at_k, run_suite

BASELINE = Path(__file__).parent / "baselines" / "evaluate_backtest.json"
QUALITY_METRICS = ("holdout_recall_at_10", "holdout_recall_at_20", "trap_cut_recall_at_6", "trap_cut_mrr",
                   "swap_trap_recall", "swap_holdout_recall")
TOLERANCE = 0.02


def test_metric_helpers():
    assert recall_at_k(["a", "b", "c"], ["a", "c", "z"], 2) == pytest.approx(1 / 3)
    assert recall_at_k(["a"], [], 5) == 0.0
    assert mean_reciprocal_rank(["x", "a", "b"], ["a", "b", "q"]) == pytest.approx((1 / 2 + 1 / 3 + 0) / 3)


def test_backtest_world_is_deterministic():
    assert run_suite(seeds=(7,)) == run_suite(seeds=(7,))


def test_backtest_does_not_regress():
    current = run_suite()
    if os.environ.get("EDH_BACKTEST_UPDATE") == "1":
        BASELINE.write_text(json.dumps(current, indent=2) + "\n")
        pytest.skip("baseline refreshed")
    baseline = json.loads(BASELINE.read_text())
    for key in QUALITY_METRICS:
        assert current[key] >= baseline[key] - TOLERANCE, f"{key} regressed: {current[key]} < {baseline[key]}"
    assert current["requests"] <= baseline["requests"], "backtest now spends more EDHREC requests"


FINGERPRINT = Path(__file__).parent / "baselines" / "evaluate_fingerprint.json"


def test_evaluator_output_is_unchanged():
    """Characterization snapshot: refactors must reproduce every score exactly.

    Behaviour changes refresh it deliberately: ``EDH_BACKTEST_UPDATE=1 pytest tests/test_evaluate_backtest.py``.
    """
    from tests.evaluate_backtest import evaluate_world, fingerprint

    current = json.loads(json.dumps({str(s): fingerprint(evaluate_world(s)) for s in (1, 2, 3)}))
    if os.environ.get("EDH_BACKTEST_UPDATE") == "1":
        FINGERPRINT.write_text(json.dumps(current, indent=1) + "\n")
        pytest.skip("fingerprint refreshed")
    assert current == json.loads(FINGERPRINT.read_text())
