"""Trial construction and metrics for the real-data backtest (synthetic inputs, fully offline)."""

from __future__ import annotations

import pytest

from edhparse.evaluate.deck_parser import parse_decklist_text
from edhparse.evaluate.models import CardMeta, CardStat, Cohort
from edhparse.evaluate.replay import SnapshotMetaLookup, snapshot_from_metas
from tests.real_backtest import CommanderInputs, auroc, build_trial, ndcg_at_k


def _cohort(label, n, rates):
    c = Cohort(label=label, num_decks=n)
    for name, p in rates.items():
        c.add(CardStat(name=name, slug=name.lower().replace(" ", "-"), num_decks=int(p * n), potential_decks=n))
    return c


def _meta(name, ci=("B", "R"), land=False, basic=False, price=1.0, cmc=2):
    tl = "Basic Land — Swamp" if basic else ("Land" if land else "Creature — Elf")
    return CardMeta(name=name, front=name, cmc=0 if land else cmc, color_identity=tuple(ci), type_line=tl,
                    price=0.0 if basic else price, price_known=True, is_land=land)


def _world():
    head = {f"Head {i}": 0.50 for i in range(6)}
    tail = {f"Tail {i}": 0.10 for i in range(6)}
    filler = {f"Filler {i}": 0.30 for i in range(6)}
    traps_z = {f"Zed {i}": 0.30 for i in range(5)}
    d0 = _cohort("d0", 10000, {**head, **tail, **filler, **traps_z, "Dual Land": 0.6})
    theme = _cohort("theme", 2000, {**{k: 0.60 for k in head}, **{k: 0.40 for k in tail},
                                    **{k: 0.35 for k in filler}, "Dual Land": 0.7})
    other = _cohort("other", 8000, {**{f"Xray {i}": 0.40 for i in range(5)}, **{f"Yank {i}": 0.40 for i in range(3)}})
    metas = [_meta(n) for n in [*head, *tail, *filler, *traps_z]]
    metas += [_meta(f"Xray {i}", ci=("R",)) for i in range(5)] + [_meta(f"Yank {i}", ci=("G",)) for i in range(3)]
    metas += [_meta("Dual Land", land=True)] + [_meta(b, ci=(), land=True, basic=True) for b in ("Swamp", "Mountain")]
    lookup = SnapshotMetaLookup(snapshot_from_metas(metas))
    inputs = CommanderInputs(slug="cmdr", name="Test Cmdr", color_identity=("B", "R"), d0=d0,
                             themes={"theme-a": theme}, others={"other": other})
    return inputs, lookup


def _build(seed=1, **kw):
    inputs, lookup = _world()
    return build_trial(inputs, "theme-a", seed, lookup, deck_size=12, **kw), inputs, lookup


def test_holdout_never_leaks_into_the_deck_and_is_stratified():
    t, _, _ = _build()
    assert len(t.holdout_head) == 3 and len(t.holdout_tail) == 3
    assert set(t.holdout_head) <= {f"Head {i}" for i in range(6)}
    assert set(t.holdout_tail) <= {f"Tail {i}" for i in range(6)}
    assert not set(t.holdout_head + t.holdout_tail) & set(t.deck_nonland)
    assert set(t.genuine) == set(t.deck_nonland) - set(t.traps_theme) - set(t.traps_cmd)
    assert len(t.deck_nonland) == len(set(t.deck_nonland))


def test_traps_come_from_the_right_sources_and_respect_colours():
    t, _, _ = _build()
    assert len(t.traps_theme) == 3 and set(t.traps_theme) <= {f"Zed {i}" for i in range(5)}
    assert len(t.traps_cmd) == 3 and set(t.traps_cmd) <= {f"Xray {i}" for i in range(5)}  # Yank is green: illegal
    assert set(t.traps_theme + t.traps_cmd) <= set(t.deck_nonland)


def test_trials_are_deterministic_per_seed_and_vary_across_seeds():
    a, _, _ = _build(seed=7)
    b, _, _ = _build(seed=7)
    assert (a.holdout_head, a.holdout_tail, a.traps_theme, a.traps_cmd) == (b.holdout_head, b.holdout_tail, b.traps_theme, b.traps_cmd)
    variants = {tuple(_build(seed=s)[0].holdout_head + _build(seed=s)[0].traps_cmd) for s in range(6)}
    assert len(variants) > 1


def test_holdout_targets_must_be_affordable():
    inputs, lookup = _world()
    pricey = lookup.get("Tail 0")
    pricey.price = 500.0
    for seed in range(8):
        t = build_trial(inputs, "theme-a", seed, lookup, deck_size=12)
        assert "Tail 0" not in t.holdout_tail


def test_decklist_text_round_trips_through_the_parser_with_36_lands():
    t, inputs, _ = _build()
    deck = parse_decklist_text(t.decklist_text(inputs.name))
    assert deck.commanders == ["Test Cmdr"]
    names = {e.name: e.quantity for e in deck.cards}
    assert sum(q for n, q in names.items() if n in ("Swamp", "Mountain", "Dual Land")) == 36
    assert names["Dual Land"] == 1 and set(t.deck_nonland) <= set(names)


# --------------------------------------------------------------------------- metrics


def test_ndcg_rewards_ranking_relevant_items_first():
    rel = {"a": 2.0, "b": 1.0}
    assert ndcg_at_k(["a", "b", "x", "y"], rel, 4) == pytest.approx(1.0)
    assert 0 < ndcg_at_k(["x", "y", "b", "a"], rel, 4) < ndcg_at_k(["b", "a", "x", "y"], rel, 4) < 1.0
    assert ndcg_at_k(["x", "y"], rel, 2) == 0.0
    assert ndcg_at_k(["a"], {}, 5) == 0.0
    assert ndcg_at_k(["x", "a"], rel, 1) == 0.0  # outside the cut-off


def test_auroc_is_threshold_free_separation():
    assert auroc([5, 6, 7], [1, 2]) == 1.0
    assert auroc([1, 2], [5, 6, 7]) == 0.0
    assert auroc([1, 1], [1, 1]) == 0.5
    assert auroc([3, 0], [1, 2]) == pytest.approx(0.5)
    assert auroc([], [1]) == 0.5


# =========================================================================== backtest fixes (phase 1/2)


def _dominant_world(tail_ratio: float):
    """Like _world, but the theme is the commander's main build: 'tail' cards barely beat their baseline rate."""
    head = {f"Head {i}": 0.50 for i in range(6)}
    tail = {f"Tail {i}": 0.10 for i in range(6)}
    d0 = _cohort("d0", 10000, {**head, **tail, **{f"Zed {i}": 0.30 for i in range(5)}, "Dual Land": 0.6})
    theme = _cohort("theme", 2000, {**{k: 0.60 for k in head}, **{k: 0.10 * tail_ratio for k in tail}, "Dual Land": 0.7})
    other = _cohort("other", 8000, {f"Xray {i}": 0.40 for i in range(5)})
    metas = [_meta(n) for n in [*head, *tail]] + [_meta(f"Zed {i}") for i in range(5)]
    metas += [_meta(f"Xray {i}", ci=("R",)) for i in range(5)] + [_meta("Dual Land", land=True)]
    metas += [_meta(b, ci=(), land=True, basic=True) for b in ("Swamp", "Mountain")]
    inputs = CommanderInputs(slug="dom", name="Test Cmdr", color_identity=("B", "R"), d0=d0,
                             themes={"t": theme}, others={"o": other})
    return inputs, SnapshotMetaLookup(snapshot_from_metas(metas))


def test_dominant_theme_still_gets_a_full_tail_stratum():
    inputs, lookup = _dominant_world(tail_ratio=1.2)  # strict 1.4x pool is empty; 1.2x qualifies for the top-up
    t = build_trial(inputs, "t", 1, lookup, deck_size=12)
    assert len(t.holdout_tail) == 3 and set(t.holdout_tail) <= {f"Tail {i}" for i in range(6)}


def test_cards_that_do_not_beat_their_baseline_never_become_tail_targets():
    inputs, lookup = _dominant_world(tail_ratio=1.0)
    t = build_trial(inputs, "t", 1, lookup, deck_size=12)
    assert t.holdout_tail == []


@pytest.mark.parametrize("ratio", [1.0, 1.2, 4.0])
def test_trial_decks_always_have_exactly_deck_size_nonland_cards(ratio):
    inputs, lookup = _dominant_world(tail_ratio=ratio)
    for seed in range(4):
        t = build_trial(inputs, "t", seed, lookup, deck_size=12)
        assert len(t.deck_nonland) == 12 and len(set(t.deck_nonland)) == 12
        assert not set(t.holdout) & set(t.deck_nonland)


def test_cluster_statistics_average_the_correlated_seeds_first():
    from tests.real_backtest import cluster_means, paired_cluster_deltas

    def row(cmd, theme, seed, x):
        return {"commander": cmd, "theme": theme, "seed": seed, "x": x}

    new = [row("A", "t1", s, 1.0) for s in (1, 2, 3)] + [row("A", "t2", s, 0.0) for s in (1, 2, 3)] + \
          [row("B", "t1", 1, 0.5), row("B", "t1", 2, 0.5), row("B", "t1", 3, 0.5)]
    old = [row("A", "t1", s, 0.0) for s in (1, 2, 3)] + [row("A", "t2", s, 0.0) for s in (1, 2, 3)] + \
          [row("B", "t1", s, 1.0) for s in (1, 2, 3)]
    assert cluster_means(new, ["x"])[("A", "t1")]["x"] == 1.0
    d = paired_cluster_deltas(new, old, ["x"])["x"]
    assert d["n"] == 3 and d["nonneg"] == 2  # clusters: +1, 0, -0.5
    assert d["mean"] == pytest.approx((1.0 + 0.0 - 0.5) / 3)
    assert d["se"] == pytest.approx(0.4410, abs=1e-3)  # sd([1, 0, -.5]) / sqrt(3)


def test_signal_ablation_profiles_zero_one_weight_and_restore_afterwards():
    import tests.real_backtest as rb
    from edhparse.evaluate import config as C

    assert rb.WEIGHT_OVERRIDE is None
    with rb.scoring_profile("no_s2"):
        w = rb.WEIGHT_OVERRIDE
        assert w[1] == 0.0 and sum(w) == pytest.approx(1.0)
        assert w[0] / w[2] == pytest.approx(C.DEFAULT_WEIGHTS[0] / C.DEFAULT_WEIGHTS[2])
    with rb.scoring_profile("s1_only"):
        assert rb.WEIGHT_OVERRIDE == (1.0, 0.0, 0.0, 0.0)
    assert rb.WEIGHT_OVERRIDE is None


def test_profiles_restore_static_methods_and_properties_exactly():
    import tests.real_backtest as rb
    from edhparse.evaluate.algorithms import anchor_bagging as A1
    from edhparse.evaluate.engine import Evaluator

    before = (Evaluator.__dict__["_prescreen"], A1.TupleCohort.__dict__["weight"])
    for profile in ("unbudgeted_prescreen", "sqrt_weight", "legacy"):
        with rb.scoring_profile(profile):
            pass
    assert (Evaluator.__dict__["_prescreen"], A1.TupleCohort.__dict__["weight"]) == before
    assert isinstance(Evaluator.__dict__["_prescreen"], staticmethod)  # still callable without an instance
