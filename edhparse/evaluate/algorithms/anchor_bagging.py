"""Algorithm 1 (S1): anchor-tuple bagging with leave-one-out scoring.

Each tuple of the user's archetype anchors defines a cohort of decks that share that build
path (real EDHREC "with card" filters; theme pages as a fallback). In-deck cards are scored
only on cohorts they did not help define (leave-one-out), external cards on all of them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from edhparse.evaluate import config as C
from edhparse.evaluate.models import CardMeta, Cohort, name_key


@dataclass
class TupleCohort:
    anchors: Tuple[str, ...]
    cohort: Optional[Cohort]
    n_eff: int
    tier: str  # "A" (live filter) or "B" (theme page)
    theme: str = ""
    affinity: float = 1.0
    exclusions: Tuple[str, ...] = ()

    @property
    def members(self) -> FrozenSet[str]:
        return frozenset(name_key(a) for a in self.anchors)

    @property
    def weight(self) -> float:
        return math.log(max(self.n_eff, 10)) * self.affinity


@dataclass
class BagScore:
    e_context: float
    bag_lift: float
    trap_penalty: float
    raw: float
    cohorts_used: int


def anchor_rank(name: str, d0: Cohort) -> float:
    return d0.p_smooth(name) * (d0.lift(name) ** 1.25)


def select_tuples(anchors: Sequence[str], metas: Dict[str, CardMeta], d0: Cohort,
                  m: int = C.DEFAULT_TUPLES) -> List[Tuple[str, str]]:
    """Top 2M anchors by distinctiveness, paired creature-with-noncreature where possible."""
    ranked = sorted(anchors, key=lambda n: anchor_rank(n, d0), reverse=True)
    top = ranked[: 2 * m]
    creatures = [n for n in top if metas[name_key(n)].is_creature]
    others = [n for n in top if not metas[name_key(n)].is_creature]
    pairs: List[Tuple[str, str]] = []
    while creatures and others:
        pairs.append((creatures.pop(0), others.pop(0)))
    rest = creatures + others  # same-type leftovers, still in rank order
    rest.sort(key=lambda n: anchor_rank(n, d0), reverse=True)
    while len(rest) >= 2:
        pairs.append((rest.pop(0), rest.pop(0)))
    pairs.sort(key=lambda p: anchor_rank(p[0], d0) + anchor_rank(p[1], d0), reverse=True)
    return pairs[:m]


def precon_exclusions(d_low: Optional[Cohort], d_high: Optional[Cohort], deck_keys: Iterable[str],
                      metas_land: Optional[set] = None, n: int = C.PRECON_EXCLUSIONS) -> List[str]:
    """Cards common in budget decks but nearly absent from expensive ones, not in the user's deck."""
    if d_low is None or d_high is None:
        return []
    deck_keys = set(deck_keys)
    found = []
    for name in d_low.names():
        if name_key(name) in deck_keys or (metas_land and name_key(name) in metas_land):
            continue
        p_low = d_low.p_raw(name)
        p_high = d_high.p_raw(name)  # unlisted on the expensive page means under its floor
        if p_low > C.PRECON_LOW_MIN and p_high < C.PRECON_HIGH_MAX:
            found.append((p_low - p_high, name))
    found.sort(reverse=True)
    return [n for _, n in found[:n]]


def theme_affinity(theme: Cohort, anchors: Sequence[str], d0: Cohort) -> float:
    """Geometric-mean inclusion ratio of the user's anchors on a theme page vs the baseline."""
    if not anchors:
        return 1.0
    logs = [
        math.log((theme.p_est(a) + C.EPS) / (d0.p_raw(a) + C.EPS))
        for a in anchors
    ]
    return math.exp(sum(logs) / len(logs))


def best_theme_for(anchors: Sequence[str], themes: Sequence[Tuple[str, Cohort]], d0: Cohort) -> Optional[Tuple[str, Cohort, float]]:
    best = None
    for slug, theme in themes:
        if theme is None or theme.num_decks <= 0:
            continue
        score = sum(math.log((theme.p_est(a) + C.EPS) / (d0.p_raw(a) + C.EPS)) for a in anchors)
        if best is None or score > best[0]:
            best = (score, slug, theme)
    if best is None:
        return None
    return best[1], best[2], best[0]


def bag_score(name: str, cohorts: Sequence[TupleCohort], d0: Cohort) -> BagScore:
    """Leave-one-out contextual inclusion, BagLift and raw S1 for one card (any pool)."""
    key = name_key(name)
    base = d0.p_smooth(name)
    num = den = 0.0
    used = 0
    for tc in cohorts:
        if tc.cohort is None or key in tc.members:
            continue
        p = tc.cohort.p_smooth(name)
        if not tc.cohort.present(name) and tc.cohort.floor_rate > d0.floor_rate:
            # Unlisted on a page with a higher listing cut-off than the baseline only says
            # "below that cut-off"; don't let the censored guess read as a boost over D0.
            p = min(p, base)
        num += tc.weight * p
        den += tc.weight
        used += 1
    e = num / den if den > 0 else base
    bag_lift = (e + C.EPS) / (base + C.EPS)
    trap = d0.p_raw(name) * (1.0 - bag_lift) if bag_lift < C.TRAP_BAGLIFT else 0.0
    raw = e * (1.0 + math.log(max(0.25, bag_lift))) - C.TRAP_PENALTY_WEIGHT * trap
    return BagScore(e_context=e, bag_lift=bag_lift, trap_penalty=trap, raw=raw, cohorts_used=used)
