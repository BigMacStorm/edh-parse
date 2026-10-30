"""Real-data backtest for the deck evaluator (recorded EDHREC pages, frozen card metadata).

Ground truth is built so the evaluator is not graded on its own inputs:

* A coherent sub-archetype deck is taken from a *theme page* (not the average deck).
* **Holdout** cards are removed from it - "head" cards (popular on the baseline page) and "tail"
  cards (build-path specific: over-represented in the theme relative to the baseline). The evaluator
  has to beat a plain "sort by baseline popularity" ranker on the tail to show S1-S4 add anything.
* **Traps** are put in their place: popular cards from a *different* theme of the same commander, and
  in-colour cards from the *other* commanders' pages that this commander's page barely lists.

Usage:
    python -m tests.real_backtest                # metrics for the current code
    python -m tests.real_backtest --ablation     # compare scoring profiles, paired by trial
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import random
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from edhparse.evaluate import config as C
from edhparse.evaluate.algorithms import anchor_bagging as A1
from edhparse.evaluate.algorithms import ensemble as A5
from edhparse.evaluate.algorithms import structural_fit as A4
from edhparse.evaluate.deck_parser import parse_decklist_text
from edhparse.evaluate.edhrec import EdhrecFetcher, commander_slug, commander_url, parse_json_page
from edhparse.evaluate.engine import Evaluator
from edhparse.evaluate.models import CardMeta, Cohort, name_key
from edhparse.evaluate.replay import ReplaySession, SnapshotMetaLookup, SnapshotTagger

REAL_DIR = Path(__file__).parent / "fixtures" / "real"
SNAPSHOT = "card_meta_snapshot.json.gz"
BASICS = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
PRICE_TIER_THEMES = {"budget", "expensive"}

COMMANDERS = (
    "Korvold, Fae-Cursed King",
    "Amalia Benavides Aguirre",
    "Gev, Scaled Scorch",
    "Isshin, Two Heavens as One",
)


# --------------------------------------------------------------------------- trials


@dataclass
class CommanderInputs:
    slug: str
    name: str
    color_identity: Tuple[str, ...]
    d0: Cohort
    themes: Dict[str, Cohort]  # theme slug -> cohort, most popular theme first
    others: Dict[str, Cohort]  # other commanders' baseline pages (source of cross-commander traps)


@dataclass
class Trial:
    commander: str
    theme: str
    seed: int
    deck_nonland: List[str]
    genuine: List[str]
    holdout_head: List[str]
    holdout_tail: List[str]
    traps_theme: List[str]
    traps_cmd: List[str]
    lands: List[Tuple[str, int]] = field(default_factory=list)

    @property
    def holdout(self) -> List[str]:
        return self.holdout_head + self.holdout_tail

    @property
    def traps(self) -> List[str]:
        return self.traps_theme + self.traps_cmd

    def decklist_text(self, commander_name: str) -> str:
        lines = [f"1 {n}" for n in self.deck_nonland] + [f"{q} {n}" for n, q in self.lands]
        return f"Commander\n1 {commander_name}\n\nDeck\n" + "\n".join(lines) + "\n"


def build_trial(inputs: CommanderInputs, theme_slug: str, seed: int, metas, *, deck_size: int = 63,
                n_head: int = 3, n_tail: int = 3, n_trap: int = 3, budget: float = C.DEFAULT_MAX_ADD_BUDGET,
                n_lands: int = 36, n_utility_lands: int = 6) -> Trial:
    d0, theme = inputs.d0, inputs.themes[theme_slug]
    ci = set(inputs.color_identity)
    rng = random.Random(f"{inputs.slug}|{theme_slug}|{seed}")
    cmd_key = name_key(inputs.name)

    def eligible(name: str) -> bool:
        m = metas.get(name)
        return m.found and m.legal and not m.is_land and set(m.color_identity) <= ci and name_key(name) != cmd_key

    ranked = sorted((n for n in theme.names() if eligible(n)), key=lambda n: (-theme.p_raw(n), n))
    base = ranked[:deck_size]

    def affordable(n: str) -> bool:
        return metas.get(n).within_budget(budget)

    def lift(n: str) -> float:
        return theme.p_raw(n) / max(d0.p_raw(n), 1e-9)

    def in_tail_band(n: str) -> bool:
        return 0.04 <= d0.p_raw(n) < 0.25 and affordable(n)

    head_pool = sorted(n for n in base if d0.p_raw(n) >= 0.25 and affordable(n))
    tail_pool = sorted(n for n in base if in_tail_band(n) and lift(n) >= 1.4)
    if len(tail_pool) < n_tail:
        # On a commander's dominant theme almost nothing is 1.4x over-represented; top the pool up with
        # the most over-represented cards that still beat their baseline rate (never below 1.1x).
        extra = sorted((n for n in base if in_tail_band(n) and n not in tail_pool and lift(n) >= 1.1),
                       key=lambda n: (-lift(n), n))
        tail_pool = sorted(tail_pool + extra[: n_tail - len(tail_pool)])
    holdout_head = rng.sample(head_pool, min(n_head, len(head_pool)))
    holdout_tail = rng.sample(tail_pool, min(n_tail, len(tail_pool)))
    held = set(holdout_head) | set(holdout_tail)

    # Cards that may end up in the genuine part of the deck (or be needed as padding) can't be traps.
    reserved = set(ranked[: deck_size + len(held) + 2 * n_trap])
    theme_trap_pool = sorted(n for n in d0.names() if n not in reserved and eligible(n) and d0.p_raw(n) >= 0.12
                             and theme.p_est(n) / d0.p_raw(n) < 0.8)
    traps_theme = rng.sample(theme_trap_pool, min(n_trap, len(theme_trap_pool)))

    cmd_pool = sorted({n for other in inputs.others.values() for n in other.names()
                       if other.p_raw(n) >= 0.20 and eligible(n) and d0.p_raw(n) < 0.03}
                      - reserved - set(traps_theme))
    traps_cmd = rng.sample(cmd_pool, min(n_trap, len(cmd_pool)))

    # Always deck_size non-land cards in total: fill the slots freed by holdout/short trap lists with the
    # next-best theme cards, or trim the lowest-ranked ones, so every trial is a 100-card deck.
    n_traps = len(traps_theme) + len(traps_cmd)
    genuine = [n for n in ranked if n not in held][: deck_size - n_traps]
    deck_nonland = genuine + traps_theme + traps_cmd

    utility = sorted((n for n in theme.names() if metas.get(n).found and metas.get(n).is_land
                      and not metas.get(n).is_basic and metas.get(n).legal
                      and set(metas.get(n).color_identity) <= ci), key=lambda n: (-theme.p_raw(n), n))[:n_utility_lands]
    colours = sorted(ci) or ["G"]
    n_basic = n_lands - len(utility)
    lands: List[Tuple[str, int]] = [(n, 1) for n in utility]
    for i, col in enumerate(colours):
        lands.append((BASICS[col], n_basic // len(colours) + (1 if i < n_basic % len(colours) else 0)))
    return Trial(commander=inputs.name, theme=theme_slug, seed=seed, deck_nonland=deck_nonland, genuine=genuine,
                 holdout_head=holdout_head, holdout_tail=holdout_tail, traps_theme=traps_theme, traps_cmd=traps_cmd,
                 lands=lands)


# --------------------------------------------------------------------------- metrics


def ndcg_at_k(ranked: Sequence[str], relevance: Dict[str, float], k: int) -> float:
    gains = [relevance.get(n, 0.0) / math.log2(i + 2) for i, n in enumerate(ranked[:k])]
    ideal = [r / math.log2(i + 2) for i, r in enumerate(sorted(relevance.values(), reverse=True)[:k])]
    return sum(gains) / sum(ideal) if ideal and sum(ideal) > 0 else 0.0


def auroc(pos: Sequence[float], neg: Sequence[float]) -> float:
    """P(random positive outscores random negative); 0.5 when either side is empty."""
    if not pos or not neg:
        return 0.5
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def _recall(ranked: Sequence[str], targets: Sequence[str], k: int) -> float:
    return len(set(ranked[:k]) & set(targets)) / len(targets) if targets else float("nan")


def _mrr(ranked: Sequence[str], targets: Sequence[str]) -> float:
    pos = {n: i + 1 for i, n in enumerate(ranked)}
    return sum(1.0 / pos[t] if t in pos else 0.0 for t in targets) / len(targets) if targets else float("nan")


def evaluate_trial(res, trial: Trial, inputs: CommanderInputs, metas, budget: float = C.DEFAULT_MAX_ADD_BUDGET) -> Dict[str, float]:
    adds = [c.name for c in res.adds]
    relevance = {**{n: 1.0 for n in trial.holdout_head}, **{n: 2.0 for n in trial.holdout_tail}}

    in_trial = set(trial.deck_nonland)
    pool = sorted((n for n in inputs.d0.names() if n not in in_trial and metas.get(n).found and metas.get(n).legal
                   and not metas.get(n).is_land and set(metas.get(n).color_identity) <= set(inputs.color_identity)
                   and metas.get(n).within_budget(budget)),
                  key=lambda n: (-inputs.d0.p_smooth(n), n))
    s5 = {c.name: c.s5 for c in res.deck_scores}
    genuine = [s5[n] for n in trial.genuine if n in s5]
    traps = [s5[n] for n in trial.traps if n in s5]
    cuts = [c.name for c in res.cuts]
    swaps = res.swaps
    scored = res.deck_scores + res.candidate_scores

    out: Dict[str, float] = {
        "ndcg_adds20": ndcg_at_k(adds, relevance, 20),
        "base_ndcg_adds20": ndcg_at_k(pool, relevance, 20),
        "tail_recall20": _recall(adds, trial.holdout_tail, 20),
        "base_tail_recall20": _recall(pool, trial.holdout_tail, 20),
        "head_recall20": _recall(adds, trial.holdout_head, 20),
        "trap_auroc": auroc(genuine, traps),
        "trap_mrr": _mrr(cuts, trial.traps),
        "trap_recall15": _recall(cuts, trial.traps, 15),
        "n_swaps": float(len(swaps)),
        # how many of the injected bad cards got cut / held-out cards got added, as a share of all of them
        "swap_trap_coverage": len({p.cut.name for p in swaps} & set(trial.traps)) / len(trial.traps) if trial.traps else float("nan"),
        "swap_holdout_coverage": len({p.add.name for p in swaps} & set(trial.holdout)) / len(trial.holdout) if trial.holdout else float("nan"),
        "swap_genuine_cuts": float(sum(p.cut.name not in set(trial.traps) for p in swaps)),
        "swap_cut_precision": float(np.mean([p.cut.name in set(trial.traps) for p in swaps])) if swaps else float("nan"),
        "swap_add_precision": float(np.mean([p.add.name in set(trial.holdout) for p in swaps])) if swaps else float("nan"),
        "role_violations": float(sum(1 for p in swaps if p.cut.requires_same_role_replacement
                                     and not p.cut.scarce_roles <= p.add.meta.roles)),
        "tuples": float(len(res.tuples)),
        "hubs": float(len(res.hubs)),
        "tier_a": float(any(t.tier == "A" for t in res.tuples)),
        "requests": float(res.telemetry.edhrec_requests),
    }
    out["lift_ndcg20"] = out["ndcg_adds20"] - out["base_ndcg_adds20"]
    out["lift_tail_recall20"] = out["tail_recall20"] - out["base_tail_recall20"]

    # Tail-only lenses (build-path cards; a popularity ranker has no head start on these) ...
    tail = {n: 1.0 for n in trial.holdout_tail}
    nan = float("nan")
    out["tail_ndcg20"] = ndcg_at_k(adds, tail, 20) if tail else nan
    out["base_tail_ndcg20"] = ndcg_at_k(pool, tail, 20) if tail else nan
    out["lift_tail_ndcg20"] = out["tail_ndcg20"] - out["base_tail_ndcg20"]
    # ... and a re-rank lens: order the evaluator's own (affordable) finalists by S5 versus by plain baseline
    # popularity. This isolates what S1-S5 add once the candidate funnel is held equal.
    fin = [c.name for c in sorted(res.candidate_scores, key=lambda c: -c.s5) if c.meta.within_budget(budget)]
    fin_pop = sorted(fin, key=lambda n: (-inputs.d0.p_smooth(n), n))
    out["rerank_ndcg20"] = ndcg_at_k(fin, relevance, 20)
    out["rerank_base_ndcg20"] = ndcg_at_k(fin_pop, relevance, 20)
    out["lift_rerank_ndcg20"] = out["rerank_ndcg20"] - out["rerank_base_ndcg20"]
    out["rerank_tail_mrr"] = _mrr(fin, trial.holdout_tail)
    out["rerank_base_tail_mrr"] = _mrr(fin_pop, trial.holdout_tail)
    out["lift_rerank_tail_mrr"] = out["rerank_tail_mrr"] - out["rerank_base_tail_mrr"]
    held = set(trial.holdout)
    out["funnel_recall"] = len(held & set(fin)) / len(held) if held else nan  # holdout cards that reach S4/S5 at all
    sat = []
    for k in range(4):
        vals = [c.s[k] for c in scored]
        out[f"sat_s{k + 1}"] = sum(abs(v) >= 95.0 for v in vals) / len(vals) if vals else 0.0
        sat.append(out[f"sat_s{k + 1}"])
        raw = np.asarray([c.raw[k] for c in res.deck_scores], dtype=float)
        iqr = float(np.percentile(raw, 75) - np.percentile(raw, 25)) if raw.size else 0.0
        out[f"spread_s{k + 1}"] = max(iqr / 1.349, 0.5 * float(raw.std())) if raw.size else 0.0
    out["sat_mean"] = float(np.mean(sat[1:]))
    return out


# --------------------------------------------------------------------------- corpus


def _slug(name: str) -> str:
    return commander_slug([name.split(" // ", 1)[0]])


def load_snapshot(real_dir: Path = REAL_DIR) -> Dict[str, dict]:
    return json.loads(gzip.decompress((Path(real_dir) / SNAPSHOT).read_bytes()).decode("utf-8"))


def load_inputs(real_dir: Path, snapshot: Dict[str, dict], n_themes: int = 2) -> Dict[str, CommanderInputs]:
    """CommanderInputs for every recorded commander, rebuilt from the recorded pages alone."""
    real_dir = Path(real_dir)
    metas = SnapshotMetaLookup(snapshot)
    d0s: Dict[str, Cohort] = {}
    sessions: Dict[str, ReplaySession] = {}
    for name in COMMANDERS:
        slug = _slug(name)
        if not (real_dir / slug / "manifest.json").exists():
            continue
        sessions[slug] = ReplaySession(real_dir / slug)
        d0s[slug] = parse_json_page(sessions[slug].get(commander_url(slug)).json(), f"D0 {name}")
    out: Dict[str, CommanderInputs] = {}
    for name in COMMANDERS:
        slug = _slug(name)
        if slug not in d0s:
            continue
        d0 = d0s[slug]
        links = sorted(((d0.raw or {}).get("panels", {}).get("taglinks") or []), key=lambda t: -(t.get("count") or 0))
        themes: Dict[str, Cohort] = {}
        for link in links:
            ts = link.get("slug")
            if not ts or ts in PRICE_TIER_THEMES:
                continue
            resp = sessions[slug].get(commander_url(slug, ts))
            if resp.status_code == 200:
                themes[ts] = parse_json_page(resp.json(), f"{name} / {ts}")
            if len(themes) >= n_themes:
                break
        m = metas.get(name)
        out[slug] = CommanderInputs(slug=slug, name=name, color_identity=tuple(m.color_identity), d0=d0, themes=themes,
                                    others={s: c for s, c in d0s.items() if s != slug})
    return out


def trial_plan(inputs: Dict[str, CommanderInputs], seeds: Sequence[int] = (1, 2, 3)) -> List[Tuple[str, str, int]]:
    return [(slug, theme, seed) for slug, ci in inputs.items() for theme in ci.themes for seed in seeds]


WEIGHT_OVERRIDE: Optional[Tuple[float, float, float, float]] = None  # set by the signal-ablation profiles


def make_evaluator(session, snapshot: Dict[str, dict], max_requests: int = 500) -> Evaluator:
    """The real Evaluator over a replay/recording session. By default the request budget is effectively
    unlimited so the backtest measures scoring quality, not request rationing (pass 22 to test that)."""
    fetcher = EdhrecFetcher(session, sleep=lambda s: None, rand=lambda a, b: 0.0, missing_cache_path=None,
                            max_requests=max_requests)
    opts = C.EvaluateOptions(max_requests=max_requests, **({"weights": WEIGHT_OVERRIDE} if WEIGHT_OVERRIDE else {}))
    return Evaluator(session, opts, fetcher=fetcher, metas=SnapshotMetaLookup(snapshot),
                     role_tagger=SnapshotTagger(snapshot), log=lambda m: None)


def run_trial(real_dir: Path, snapshot: Dict[str, dict], inputs: CommanderInputs, trial: Trial, max_requests: int = 500):
    session = ReplaySession(Path(real_dir) / inputs.slug)
    ev = make_evaluator(session, snapshot, max_requests)
    res = ev.run(parse_decklist_text(trial.decklist_text(inputs.name)))
    metrics = evaluate_trial(res, trial, inputs, SnapshotMetaLookup(snapshot))
    metrics["unrecorded_urls"] = float(len(session.unrecorded))
    metrics["untagged_cards"] = float(len(ev.role_tagger.untagged))
    return res, metrics


def run_corpus(real_dir: Path = REAL_DIR, seeds: Sequence[int] = (1, 2, 3), n_themes: int = 2) -> List[dict]:
    snapshot = load_snapshot(real_dir)
    inputs = load_inputs(real_dir, snapshot, n_themes)
    metas = SnapshotMetaLookup(snapshot)
    rows = []
    for slug, theme, seed in trial_plan(inputs, seeds):
        trial = build_trial(inputs[slug], theme, seed, metas)
        _, m = run_trial(real_dir, snapshot, inputs[slug], trial)
        rows.append({"commander": inputs[slug].name, "theme": theme, "seed": seed, **m,
                     "deck_total": float(1 + len(trial.deck_nonland) + sum(q for _, q in trial.lands)),
                     "n_head": float(len(trial.holdout_head)), "n_tail": float(len(trial.holdout_tail)),
                     "trial_fp": trial_fingerprint(trial)})
    return rows


def trial_fingerprint(trial: Trial) -> str:
    """Short digest of everything that defines a trial; changes when the builder or the corpus drifts."""
    payload = [trial.deck_nonland, trial.holdout_head, trial.holdout_tail, trial.traps_theme, trial.traps_cmd,
               [list(x) for x in trial.lands]]
    return hashlib.sha1(json.dumps(payload).encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- profiles / ablation


@contextmanager
def scoring_profile(name: str):
    """Temporarily revert individual hardening changes to measure what each one is worth."""
    undo: List[Tuple[object, str, object]] = []

    def patch(obj, attr, value):
        # Read the raw class attribute: getattr() on a class unwraps staticmethod/property objects, and
        # putting the unwrapped function back would silently turn it into an instance method.
        raw = vars(obj)[attr] if isinstance(obj, type) and attr in vars(obj) else getattr(obj, attr)
        undo.append((obj, attr, raw))
        setattr(obj, attr, value)

    legacy = name == "legacy"
    try:
        drop = {"s1_only": (1, 2, 3), "no_s1": (0,), "no_s2": (1,), "no_s3": (2,), "no_s4": (3,)}.get(name)
        if drop is not None:  # signal ablation: zero some weights and renormalise the rest
            w = [0.0 if i in drop else x for i, x in enumerate(C.DEFAULT_WEIGHTS)]
            patch(sys.modules[__name__], "WEIGHT_OVERRIDE", tuple(x / sum(w) for x in w))
        if name == "unbudgeted_prescreen":  # the behaviour before finalist slots were reserved for affordable cards
            patch(Evaluator, "_prescreen", staticmethod(_unbudgeted_prescreen))
        if name == "sqrt_weight":  # candidate that was tried and rejected; kept to re-test on new data
            patch(A1.TupleCohort, "weight",
                  property(lambda self: math.sqrt(min(max(self.n_eff, 5), 1000)) * self.affinity))
        if name == "no_min_scale" or legacy:
            patch(C, "MIN_SIGNAL_SCALES", (1e-4, 1e-4, 1e-4, 1e-4))
        if name == "old_swap_bonuses" or legacy:
            patch(C, "SWAP_CURVE_BONUS", 12.0)
            patch(C, "SWAP_CURVE_PENALTY_PER_CMC", 6.0)
            patch(C, "SWAP_TYPE_BONUS", 18.0)
            patch(C, "SWAP_ROLE_BONUS", 15.0)
        if name == "fixed_role_floors" or legacy:
            patch(A4, "role_floors_from_avg", lambda avg: dict(C.ROLE_FLOORS))
        yield
    finally:
        for obj, attr, old in reversed(undo):
            setattr(obj, attr, old)


def _unbudgeted_prescreen(deck_scores, ext_scores, weights, budget):
    """Pre-fix prescreen: the top FINALISTS on S1-S3 regardless of price."""
    frames = [A5.reference_frame([c.raw[k] for c in deck_scores], C.MIN_SIGNAL_SCALES[k]) for k in range(3)]
    for c in ext_scores:
        c.finalist = False
        c.prelim_score = sum(weights[k] * A5.project(c.raw[k], *frames[k]) for k in range(3))
    ext_scores.sort(key=lambda c: c.prelim_score, reverse=True)
    finalists = ext_scores[: C.FINALISTS]
    for c in finalists:
        c.finalist = True
    return finalists


PROFILES = ("current", "sqrt_weight", "no_min_scale", "old_swap_bonuses", "fixed_role_floors", "unbudgeted_prescreen",
            "s1_only", "no_s1", "no_s2", "no_s3", "no_s4", "legacy")
QUALITY = ("ndcg_adds20", "lift_ndcg20", "tail_ndcg20", "lift_tail_ndcg20", "tail_recall20", "lift_tail_recall20",
           "lift_rerank_ndcg20", "lift_rerank_tail_mrr", "funnel_recall", "trap_auroc", "trap_mrr",
           "swap_cut_precision", "swap_add_precision")
HEADLINE = ("ndcg_adds20", "lift_tail_ndcg20", "lift_rerank_ndcg20", "lift_rerank_tail_mrr", "funnel_recall",
            "trap_auroc", "swap_cut_precision")


def _mean(xs: Iterable[float]) -> float:
    xs = [x for x in xs if not math.isnan(x)]
    return float(np.mean(xs)) if xs else float("nan")


def summarize(rows: List[dict], keys: Sequence[str] = QUALITY + ("sat_mean", "role_violations", "n_swaps")) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {"ALL": {k: _mean(r[k] for r in rows) for k in keys}}
    for name in dict.fromkeys(r["commander"] for r in rows):
        out[name] = {k: _mean(r[k] for r in rows if r["commander"] == name) for k in keys}
    return out


def paired_deltas(new: List[dict], old: List[dict], keys: Sequence[str] = QUALITY) -> Dict[str, Dict[str, float]]:
    """Mean (new - old) per metric, per commander and overall, over trials that exist in both."""
    idx = {(r["commander"], r["theme"], r["seed"]): r for r in old}
    pairs = [(r, idx[(r["commander"], r["theme"], r["seed"])]) for r in new if (r["commander"], r["theme"], r["seed"]) in idx]
    out: Dict[str, Dict[str, float]] = {}
    for label, sel in [("ALL", pairs)] + [(c, [p for p in pairs if p[0]["commander"] == c])
                                          for c in dict.fromkeys(r["commander"] for r in new)]:
        out[label] = {k: _mean(a[k] - b[k] for a, b in sel) for k in keys}
    return out


def cluster_means(rows: List[dict], keys: Sequence[str] = QUALITY) -> Dict[Tuple[str, str], Dict[str, float]]:
    """Mean per (commander, theme). The seeds inside a cluster share ~90% of their deck, so they are
    averaged first: the cluster, not the trial, is the unit of statistical independence."""
    groups: Dict[Tuple[str, str], List[dict]] = {}
    for r in rows:
        groups.setdefault((r["commander"], r["theme"]), []).append(r)
    return {c: {k: _mean(r[k] for r in rs) for k in keys} for c, rs in groups.items()}


def paired_cluster_deltas(new: List[dict], old: List[dict], keys: Sequence[str] = QUALITY) -> Dict[str, Dict[str, float]]:
    """Per metric: mean cluster delta (new - old), its standard error, #clusters, and #clusters not worse."""
    cn, co = cluster_means(new, keys), cluster_means(old, keys)
    out: Dict[str, Dict[str, float]] = {}
    for k in keys:
        ds = [cn[c][k] - co[c][k] for c in cn if c in co and not math.isnan(cn[c][k] - co[c][k])]
        n = len(ds)
        out[k] = {"mean": float(np.mean(ds)) if ds else float("nan"),
                  "se": float(np.std(ds, ddof=1) / math.sqrt(n)) if n > 1 else float("nan"),
                  "n": n, "nonneg": sum(d >= -1e-9 for d in ds)}
    return out


def _fmt(v: float) -> str:
    return "   n/a " if v != v else f"{v:7.3f}"


def main(argv: Sequence[str]) -> int:
    rows = {}
    profiles = PROFILES if "--ablation" in argv else ("current",)
    for p in profiles:
        with scoring_profile(p):
            rows[p] = run_corpus()
        missing = sum(r["unrecorded_urls"] > 0 for r in rows[p])
        if missing:
            print(f"!! profile '{p}': {missing} trial(s) asked for pages that were never recorded; "
                  "its results are unreliable (re-run scripts/record_edhrec_fixtures.py)")
    detail = "--detail" in argv
    cur = summarize(rows["current"], QUALITY + ("sat_mean", "role_violations", "n_swaps"))
    print("== current code (mean over trials) ==")
    for who, vals in cur.items():
        if who == "ALL" or detail:
            print(f"{who:28s} " + " ".join(f"{k}={_fmt(v)}" for k, v in vals.items()))
    cm = cluster_means(rows["current"], HEADLINE)
    print("\n== per cluster (commander / theme) ==")
    for (cmd, theme), vals in cm.items():
        print(f"{cmd[:20]:20s} {theme[:16]:16s} " + " ".join(f"{k[:14]}={_fmt(v)}" for k, v in vals.items()))
    for p in profiles[1:]:
        print(f"\n== current minus '{p}'  (+ = current better; mean over {len(cm)} clusters, +/- SE, clusters not worse) ==")
        d = paired_cluster_deltas(rows["current"], rows[p], HEADLINE)
        print("   " + "  ".join(f"{k[:18]}={v['mean']:+.3f}+/-{v['se']:.3f} ({v['nonneg']}/{v['n']})" for k, v in d.items()))
        if detail:
            for who, vals in paired_deltas(rows["current"], rows[p], HEADLINE).items():
                print(f"   {who:28s} " + " ".join(f"{k[:14]}={v:+.3f}" for k, v in vals.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
