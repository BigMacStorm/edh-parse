"""Algorithm 5 (S5): in-deck reference normalisation, guardrails and the 1-for-1 swap matcher."""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import linear_sum_assignment

from edhparse.evaluate import config as C
from edhparse.evaluate.models import CardScores, Cohort, LandUpgrade, SwapPair

TAG_PROTECTED = "PROTECTED (KEEP)"
TAG_SECRET_TECH = "SECRET TECH (KEEP)"
TAG_WRONG_ARCHETYPE = "WRONG-ARCHETYPE TRAP (CUT)"
TAG_TRAINING_WHEELS = "WIN-MORE / CASUAL TRAP (CUT)"
TAG_BUDGET_SURVIVOR = "BUDGET SURVIVOR"
TAG_CORE_PILLAR = "CORE PILLAR"
TAG_FORMAT_STAPLE = "FORMAT STAPLE"
TAG_ROLE_GUARD = "ROLE GUARDRAIL"


# --------------------------------------------------------------------------- normalisation


def reference_frame(in_deck_raw: Sequence[float], min_scale: float = 1e-4) -> Tuple[float, float]:
    """(median, scale) fitted on the user's in-deck non-land cards only."""
    arr = np.asarray(list(in_deck_raw), dtype=float)
    if arr.size == 0:
        return 0.0, min_scale
    med = float(np.median(arr))
    iqr = float(np.percentile(arr, 75) - np.percentile(arr, 25))
    std = float(np.std(arr))
    scale = max(iqr / 1.349, 0.5 * std, min_scale)
    return med, scale


def project(raw: float, med: float, scale: float) -> float:
    z = (raw - med) / scale
    return round(100.0 * math.tanh(C.TANH_GAIN * z), 1)


def normalise_scores(cards: Sequence[CardScores]) -> List[Tuple[float, float]]:
    frames = []
    deck = [c for c in cards if c.in_deck]
    for k in range(4):
        med, scale = reference_frame([c.raw[k] for c in deck], C.MIN_SIGNAL_SCALES[k])
        frames.append((med, scale))
        for c in cards:
            c.s[k] = project(c.raw[k], med, scale)
    return frames


# --------------------------------------------------------------------------- S5


def effective_weights(base: Sequence[float], s1_factor: float = 1.0, s3_available: bool = True) -> Tuple[float, ...]:
    w = list(base)
    w[0] *= s1_factor
    if not s3_available:
        w[2] = 0.0
    total = sum(w)
    return tuple(x / total for x in w) if total > 0 else tuple(base)


def weighted(s: Sequence[float], w: Sequence[float]) -> float:
    return round(sum(a * b for a, b in zip(s, w)), 1)


def apply_s5(cards: Sequence[CardScores], weights: Sequence[float], secret_weights: Sequence[float] = C.SECRET_TECH_WEIGHTS) -> None:
    for c in cards:
        c.s5 = weighted(c.s, weights)
        c.tags = []
        if c.in_deck:
            if c.protected:
                c.tags.append(TAG_PROTECTED)
            if c.p_raw_d0 < 0.05 and c.s[0] >= 25.0 and c.s[2] >= 25.0:
                c.s5 = weighted(c.s, secret_weights)
                c.tags.append(TAG_SECRET_TECH)
            if c.p_raw_d0 >= 0.16 and c.bag_lift < 0.78 and c.s[2] < 0.0:
                c.s5 = max(-100.0, round(c.s5 - 15.0, 1))
                c.tags.append(TAG_WRONG_ARCHETYPE)
            if c.meta.cmc >= C.CLUNKY_CMC and c.orr < 0.50 and c.s[1] <= -30.0:
                c.s5 = max(-100.0, round(c.s5 - 10.0, 1))
                c.tags.append(TAG_TRAINING_WHEELS)
        if c.meta.within_budget(5.0) and c.orr >= 1.15 and c.s[1] >= 30.0:
            c.tags.append(TAG_BUDGET_SURVIVOR)
        if c.in_deck:
            if c.s5 >= 50.0:
                c.tags.append(TAG_CORE_PILLAR)
            if c.pool == "U":
                c.tags.append(TAG_FORMAT_STAPLE)
            if c.requires_same_role_replacement:
                c.tags.append(TAG_ROLE_GUARD)


# --------------------------------------------------------------------------- swaps


def cut_candidates(deck: Sequence[CardScores], n: int = C.CUT_POOL) -> List[CardScores]:
    pool = []
    for c in deck:
        if not c.in_deck or c.meta.is_land or c.protected or TAG_SECRET_TECH in c.tags:
            continue
        if c.s5 >= 10.0:
            continue
        if c.pool == "U" and c.s5 >= -30.0:
            continue
        pool.append(c)
    pool.sort(key=lambda c: c.s5)
    return pool[:n]


def add_candidates(external: Sequence[CardScores], budget: float, n: int = C.ADD_POOL) -> List[CardScores]:
    pool = [
        c for c in external
        if not c.in_deck and c.finalist and c.meta.within_budget(budget) and c.s5 > 15.0 and not c.meta.is_land
    ]
    pool.sort(key=lambda c: c.s5, reverse=True)
    return pool[:n]


def swap_gain(cut: CardScores, add: CardScores) -> Tuple[float, str]:
    delta = add.s5 - cut.s5
    notes = []
    if add.meta.cmc <= cut.meta.cmc:
        curve = C.SWAP_CURVE_BONUS
        notes.append("same or lower mana value")
    else:
        curve = -C.SWAP_CURVE_PENALTY_PER_CMC * (add.meta.cmc - cut.meta.cmc)
        notes.append(f"+{add.meta.cmc - cut.meta.cmc:g} mana value")
    type_bonus = 0.0
    if (add.s[3] > 10.0 and cut.s[3] < -10.0) or add.meta.primary_type == cut.meta.primary_type:
        type_bonus = C.SWAP_TYPE_BONUS
        notes.append("fills the same slot type" if add.meta.primary_type == cut.meta.primary_type else "moves a bloated slot to a starved one")
    role_adj = 0.0
    if cut.meta.roles and (cut.meta.roles & add.meta.roles):
        role_adj += C.SWAP_ROLE_BONUS
        notes.append("keeps role " + "/".join(sorted(cut.meta.roles & add.meta.roles)))
    if cut.requires_same_role_replacement and not cut.scarce_roles <= add.meta.roles:
        role_adj -= C.SWAP_SCARCE_ROLE_PENALTY
        notes.append("would drop scarce " + "/".join(sorted(cut.scarce_roles)))
    if add.s[0] - cut.s[0] >= 25:
        notes.append("much better fit with your build path (S1)")
    if add.s[2] - cut.s[2] >= 25:
        notes.append("ties into your engine cards (S3)")
    return delta + curve + type_bonus + role_adj, "; ".join(notes)


def match_swaps(cuts: Sequence[CardScores], adds: Sequence[CardScores], max_swaps: int = C.MAX_SWAPS) -> List[SwapPair]:
    if not cuts or not adds:
        return []
    gain = np.zeros((len(cuts), len(adds)))
    valid = np.zeros((len(cuts), len(adds)), dtype=bool)
    notes: Dict[Tuple[int, int], str] = {}
    for i, c in enumerate(cuts):
        for j, a in enumerate(adds):
            g, why = swap_gain(c, a)
            notes[(i, j)] = why
            # Pairs that can never be reported are worth nothing, so they cannot pull the
            # assignment away from a valid partner.
            if g > C.MIN_SWAP_GAIN and (a.s5 - c.s5) >= C.MIN_SWAP_DELTA:
                gain[i, j] = g
                valid[i, j] = True
    rows, cols = linear_sum_assignment(-gain)
    pairs = []
    for i, j in zip(rows, cols):
        if not valid[i, j]:
            continue
        pairs.append(SwapPair(cut=cuts[i], add=adds[j], gain=round(float(gain[i, j]), 1),
                              delta=round(adds[j].s5 - cuts[i].s5, 1), rationale=notes[(i, j)]))
    pairs.sort(key=lambda p: p.gain, reverse=True)
    return pairs[:max_swaps]


# --------------------------------------------------------------------------- lands


def land_score(name: str, d0: Cohort, d_high: Optional[Cohort]) -> float:
    hi = d_high.p_smooth(name) if d_high is not None else d0.p_smooth(name)
    return 0.5 * d0.p_smooth(name) + 0.5 * hi


def land_upgrades(user_lands: Sequence[str], candidates: Sequence[Tuple[str, float]], d0: Cohort,
                  d_high: Optional[Cohort], n: int = C.LAND_UPGRADES) -> List[LandUpgrade]:
    """``candidates`` = (name, price) for legal, on-colour non-basic lands not in the deck."""
    mine = sorted(((land_score(l, d0, d_high), l) for l in user_lands))
    theirs = sorted(((land_score(c, d0, d_high), c, price) for c, price in candidates), reverse=True)
    out = []
    for (cut_score, cut), (add_score, add, price) in zip(mine, theirs):
        if add_score <= cut_score:
            break
        out.append(LandUpgrade(cut=cut, cut_score=round(cut_score, 3), add=add, add_score=round(add_score, 3), price=price))
        if len(out) >= n:
            break
    return out
