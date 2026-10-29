# Evaluator backtest: how it works and what it found

The deck evaluator (`edhparse/evaluate/`) is checked against **recorded real EDHREC data** for four
commanders (Korvold, Amalia, Gev, Isshin), fully offline.

## Layout

| Path | Purpose |
|---|---|
| `scripts/record_edhrec_fixtures.py` | One-off, network-using recorder. Plays every trial deck through the real `Evaluator` against a recording session, so exactly the pages it asks for are captured. |
| `tests/fixtures/real/<slug>/` | Trimmed, gzipped pages + `manifest.json` (URL -> file). About 3.4 MB for all four. |
| `tests/fixtures/real/card_meta_snapshot.json.gz` | Frozen Scryfall metadata, prices and **role tags for every card** (not just the ones scored on the recording run). |
| `edhparse/evaluate/replay.py` | Trimmer, `RecordingSession`, `ReplaySession`, snapshot lookup/tagger. |
| `tests/real_backtest.py` | Trial builder, metrics, ablation profiles, cluster statistics. `python -m tests.real_backtest [--ablation] [--detail]`. |
| `tests/test_real_corpus.py` | The gate (see below). `tests/baselines/real_backtest.json` is its baseline. |
| `tests/evaluate_backtest.py` | The older **synthetic** world (fast, but cannot discriminate between scoring variants). |

## Trials (ground truth)

For each commander, its two most popular theme pages give a coherent sub-archetype deck (63 non-land
cards + 36 lands + commander = 100). Three seeds per theme -> 24 trials in 8 `(commander, theme)` clusters.

* **Holdout** (removed): 3 *head* cards (baseline inclusion >= 25%) and 3 *tail* cards (build-path specific:
  over-represented in the theme vs the baseline, >= 1.4x, topped up from >= 1.1x on dominant themes).
* **Traps** (added): 3 popular cards from a *different* theme + 3 in-colour cards from the *other* commanders.
* Metrics: NDCG@20 of the holdout among recommended adds, lift over a popularity ranker (full funnel, tail-only and
  re-rank-only), trap AUROC / MRR, swap precision, role-floor violations, saturation.

The 3 seeds in a cluster share ~90% of their cards, so **the cluster is the unit of independence** (n = 8). Ablation
output reports cluster-paired mean deltas, their standard error and the number of clusters not worse.

## Workflows

```
python scripts/record_edhrec_fixtures.py          # re-record (about 100 polite requests; slow web pacing; run rarely)
python scripts/record_edhrec_fixtures.py --retag-only   # re-tag the snapshot only (Scryfall, no EDHREC traffic)
python -m tests.real_backtest --ablation          # compare scoring variants on the recorded corpus
pytest tests/test_real_corpus.py                  # the gate
EDH_BACKTEST_UPDATE=1 pytest tests/test_real_corpus.py   # accept new numbers (explain why in the commit)
```

The gate is **exact** (replay is deterministic): any change that moves a score must refresh the baseline on purpose.
It also enforces: 0 unrecorded pages, all trials on Tier A (live filters) with 8 tuples / 5 hubs, 100-card decks with a
full holdout, no card scored with un-looked-up roles, 0 role-floor violations, determinism, and that the production
22-request budget still leaves room for all 5 hub pages.

**Fixtures are tied to the code that chose which pages to request.** Changing triage, `select_tuples`, `select_hubs`
or the trial builder can ask for pages that were never recorded (the gate fails loudly: `unrecorded_urls`). The recorder
plays each trial under both cohort weightings because hub choice depends on them.

## Findings (recorded corpus, 8 clusters; "current minus variant", + = current is better)

| Variant | ndcg_adds20 | Reading |
|---|---|---|
| drop S1 | +0.301 +/- 0.049 (8/8) | S1 (anchor-tuple bagging) is the most valuable signal by far |
| drop S3 | +0.088 +/- 0.039 (6/8) | S3 (hub webbing) helps |
| drop S2 | -0.025 +/- 0.052 (4/8) | S2 (price survivor) is indistinguishable from zero |
| drop S4 | -0.071 +/- 0.039 (1/8) | **S4 (structural fit) hurts add ranking** (tail NDCG -0.098 +/- 0.045) |
| S1 only | -0.119 +/- 0.049 (2/8) | **S1 alone beats the full ensemble** at ranking adds |
| sqrt(N) cohort weight | +0.007 +/- 0.004 (7/8) | ln(N) kept; sqrt(N) was tried and rejected |
| no `MIN_SIGNAL_SCALES` | 0.000 | the floors never bind on real data |
| old swap bonuses | swap_cut_precision +0.033 +/- 0.023 (8/8) | rebalanced bonuses are never worse |
| fixed role floors | swap_cut_precision +0.043 +/- 0.022 (8/8) | average-deck role floors slightly better |
| unbudgeted prescreen | re-rank NDCG +0.006 +/- 0.004 (7/8) | budget-first finalists: small, consistent gain |

Current code, mean over trials: trap AUROC 0.92 (genuine cards outscore traps 92% of the time), 95% of held-out
cards reach the scoring stage, tail NDCG lift over a popularity ranker +0.003 (no better than popularity on build-path
cards), re-ranking the evaluator's own finalists by S5 is worse than by plain popularity on the mixed head+tail set
(-0.079).

## Known limits

* Ground truth is EDHREC-derived (theme pages), not win-rate or tournament derived. Eight clusters is small.
* `RoleTagger` has no backoff for Scryfall HTTP 429, so a very large cold batch (thousands of cards) abandons the whole
  pass; the recorder works around it by retrying (progress is cached). A normal evaluation tags about a hundred cards.
* Candidate ideas the data points at, not yet tried: reduce or remove S4's weight in add ranking (keep it as a swap
  guard), re-weight S1/S3 versus S2. Evaluate any such change with `--ablation` and refresh the baseline deliberately.
