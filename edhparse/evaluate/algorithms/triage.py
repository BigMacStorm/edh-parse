"""Deck triage: lands (L), ubiquitous staples (U), archetype anchors (A), niche / pet cards (Q)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from edhparse.evaluate import config as C
from edhparse.evaluate.models import CardMeta, Cohort, name_key


@dataclass
class Triage:
    basic_lands: List[str] = field(default_factory=list)
    utility_lands: List[str] = field(default_factory=list)
    staples: List[str] = field(default_factory=list)
    anchors: List[str] = field(default_factory=list)
    niche: List[str] = field(default_factory=list)
    signature: List[str] = field(default_factory=list)  # high-inclusion, high-lift commander cards
    pool: Dict[str, str] = field(default_factory=dict)  # name_key -> L / U / A / Q

    @property
    def nonland(self) -> List[str]:
        return self.staples + self.anchors + self.niche


def is_ubiquitous(name: str, d0: Cohort) -> bool:
    if name_key(name) in C.FORMAT_STAPLES:
        return True
    return d0.p_raw(name) > C.STAPLE_INCLUSION and d0.lift(name) < C.STAPLE_MAX_LIFT


def triage_deck(names: List[str], metas: Dict[str, CardMeta], d0: Cohort) -> Triage:
    t = Triage()
    for name in names:
        meta = metas[name_key(name)]
        key = name_key(name)
        if meta.is_land:
            (t.basic_lands if meta.is_basic else t.utility_lands).append(name)
            t.pool[key] = "L"
            continue
        p = d0.p_raw(name)
        lift = d0.lift(name)
        if is_ubiquitous(name, d0):
            t.staples.append(name)
            t.pool[key] = "U"
        elif C.ANCHOR_MIN_INCLUSION <= p <= C.STAPLE_INCLUSION or (d0.present(name) and lift >= C.SIGNATURE_LIFT):
            t.anchors.append(name)
            t.pool[key] = "A"
            if p > C.STAPLE_INCLUSION:
                t.signature.append(name)
        else:
            t.niche.append(name)
            t.pool[key] = "Q"
    return t
