"""Algorithm 2 (S2): budget-vs-expensive survivor analysis.

Rewards affordable cards that keep their slot in expensive decks ("budget survivors") and
penalises clunky cards that only budget decks run. Cards missing from a page are treated as
censored below that page's listing floor rather than as 0%.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from edhparse.evaluate import config as C
from edhparse.evaluate.models import CardMeta, Cohort


@dataclass
class SurvivorScore:
    orr: float
    phi: float
    p_active: float
    raw: float
    zero_signal: bool


def phi_price(orr: float, meta: CardMeta) -> float:
    if orr >= 1.0:
        return 1.0 + 1.0 / math.log(max(meta.price, 1.5) + 1.0)
    if meta.cmc >= C.CLUNKY_CMC:
        return C.CLUNKY_PHI
    return 1.0


def survivor_score(name: str, meta: CardMeta, d_high: Optional[Cohort], d_low: Optional[Cohort],
                   damping: float = C.NEGATIVE_SURVIVOR_DAMPING) -> SurvivorScore:
    if d_high is None or d_low is None or (not d_high.present(name) and not d_low.present(name)):
        return SurvivorScore(orr=1.0, phi=1.0, p_active=0.0, raw=0.0, zero_signal=True)
    p_high = d_high.p_est(name)
    p_low = d_low.p_est(name)
    orr = (p_high + C.EPS) / (p_low + C.EPS)
    phi = phi_price(orr, meta)
    p_active = max(p_high, 0.5 * p_low)
    raw = p_active * math.log(orr) * phi
    if raw < 0:
        raw *= damping
    return SurvivorScore(orr=orr, phi=phi, p_active=p_active, raw=raw, zero_signal=False)
