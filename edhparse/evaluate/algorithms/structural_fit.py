"""Algorithm 4 (S4): mana curve, card-type and functional-role fit.

Compares the deck with the optimised average deck (blended with the commander page's own
structural panels), applying asymmetric curve gravity so the evaluator never pushes a sleek
deck toward expensive cards, and boosting cards that fill a starved functional role.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional, Sequence

from edhparse.evaluate import config as C
from edhparse.evaluate.models import CardMeta, Cohort


def _normalise(d: Dict[str, float], keys: Sequence[str]) -> Dict[str, float]:
    total = sum(d.get(k, 0.0) for k in keys)
    if total <= 0:
        return {k: 0.0 for k in keys}
    return {k: d.get(k, 0.0) / total for k in keys}


def type_weights(meta: CardMeta) -> Dict[str, float]:
    types = meta.types
    if not types:
        return {}
    w = 1.0 / len(types)  # "Artifact Creature" -> 0.5 / 0.5
    return {t: w for t in types}


def cmc_distribution(metas: Iterable[CardMeta]) -> Dict[str, float]:
    counts = {b: 0.0 for b in C.CMC_BUCKETS}
    for m in metas:
        counts[m.cmc_bucket] += 1
    return _normalise(counts, C.CMC_BUCKETS)


def type_distribution(metas: Iterable[CardMeta]) -> Dict[str, float]:
    counts = {t: 0.0 for t in C.CARD_TYPES}
    for m in metas:
        for t, w in type_weights(m).items():
            counts[t] += w
    return _normalise(counts, C.CARD_TYPES)


def curve_from_panel(mana_curve: Optional[dict]) -> Optional[Dict[str, float]]:
    if not mana_curve:
        return None
    counts = {b: 0.0 for b in C.CMC_BUCKETS}
    for k, v in mana_curve.items():
        try:
            cmc = float(k)
            n = float(v)
        except (TypeError, ValueError):
            continue
        bucket = "0-1" if cmc <= 1 else ("6+" if cmc >= 6 else str(int(cmc)))
        counts[bucket] += n
    return _normalise(counts, C.CMC_BUCKETS)


def types_from_page(raw: Optional[dict]) -> Optional[Dict[str, float]]:
    if not raw:
        return None
    counts = {t: float(raw.get(t.lower()) or 0) for t in C.CARD_TYPES}
    if sum(counts.values()) <= 0:
        return None
    return _normalise(counts, C.CARD_TYPES)


def blend(primary: Optional[Dict[str, float]], secondary: Optional[Dict[str, float]], keys: Sequence[str],
          w: float = C.AVG_DECK_BLEND) -> Dict[str, float]:
    if primary and secondary:
        return _normalise({k: w * primary.get(k, 0) + (1 - w) * secondary.get(k, 0) for k in keys}, keys)
    return dict(primary or secondary or {k: 1.0 / len(keys) for k in keys})


def apply_efficiency_floor(t_cmc: Dict[str, float]) -> Dict[str, float]:
    low = t_cmc["0-1"] + t_cmc["2"]
    if low >= C.EFFICIENCY_FLOOR or low <= 0:
        return dict(t_cmc)
    out = dict(t_cmc)
    up = C.EFFICIENCY_FLOOR / low
    high_total = 1.0 - low
    down = (1.0 - C.EFFICIENCY_FLOOR) / high_total if high_total > 0 else 0.0
    for b in C.CMC_BUCKETS:
        out[b] = t_cmc[b] * (up if b in ("0-1", "2") else down)
    return out


@dataclass
class StructuralModel:
    target_cmc: Dict[str, float]
    target_type: Dict[str, float]
    user_cmc: Dict[str, float]
    user_type: Dict[str, float]
    delta_cmc: Dict[str, float]
    delta_type: Dict[str, float]
    role_counts: Dict[str, int]
    land_count: int
    mdfc_count: int
    nonland_count: int
    user_cmc_counts: Dict[str, int] = field(default_factory=dict)
    user_type_counts: Dict[str, float] = field(default_factory=dict)
    role_floors: Dict[str, int] = field(default_factory=lambda: dict(C.ROLE_FLOORS))

    def deficit(self, role: str) -> float:
        floor = self.role_floors[role]
        have = self.role_counts.get(role, 0)
        return (floor - have) / floor if have < floor else 0.0

    def role_boost(self, meta: CardMeta) -> float:
        boost = C.ROLE_BOOST_WEIGHT * max((self.deficit(r) for r in meta.roles if r in self.role_floors), default=0.0)
        if meta.is_mdfc_land and (self.land_count + self.mdfc_count) < C.MDFC_LAND_TARGET:
            boost += C.MDFC_LAND_BOOST
        return boost

    def slot_pressure(self, meta: CardMeta) -> float:
        tw = type_weights(meta)
        d_type = sum(w * self.delta_type.get(t, 0.0) for t, w in tw.items())
        return 0.60 * self.delta_cmc[meta.cmc_bucket] + 0.40 * d_type

    def raw_score(self, meta: CardMeta) -> float:
        return -1.0 * self.slot_pressure(meta) + self.role_boost(meta)

    def scarce_roles_if_cut(self, meta: CardMeta) -> set:
        return {r for r in meta.roles if r in self.role_floors and self.role_counts.get(r, 0) - 1 < self.role_floors[r]}


def curve_delta(user: Dict[str, float], target: Dict[str, float]) -> Dict[str, float]:
    out = {}
    for b in C.CMC_BUCKETS:
        raw = (user[b] - target[b]) / max(target[b], 0.05)
        if b in ("0-1", "2") and raw > 0:
            raw *= C.LOW_CMC_BLOAT_FACTOR
        elif b in ("5", "6+"):
            raw = raw * C.HIGH_CMC_BLOAT_FACTOR if raw > 0 else max(C.HIGH_CMC_STARVE_CLAMP, raw)
        out[b] = raw
    return out


def role_floors_from_avg(avg_nonland: Sequence[CardMeta]) -> Dict[str, int]:
    """Per-role minimums from the role-tagged average deck, blended with the defaults and bounded.

    Falls back to the fixed defaults when nothing in the average deck carries a role tag
    (untagged, or the tag lookup failed), since zero tagged cards would read as "no roles needed".
    """
    if not any(m.roles for m in avg_nonland):
        return dict(C.ROLE_FLOORS)
    floors = {}
    for role, default in C.ROLE_FLOORS.items():
        avg_count = sum(1 for m in avg_nonland if role in m.roles)
        blended = C.AVG_DECK_BLEND * avg_count + (1 - C.AVG_DECK_BLEND) * default
        lo, hi = C.ROLE_FLOOR_BOUNDS[role]
        floors[role] = max(lo, min(hi, int(blended + 0.5)))
    return floors


def build_model(deck_nonland: Sequence[CardMeta], deck_lands: Sequence[CardMeta],
                avg_deck: Sequence[CardMeta], d0: Optional[Cohort]) -> StructuralModel:
    avg_nonland = [m for m in avg_deck if not m.is_land]
    raw0 = d0.raw if d0 is not None else None
    panel_curve = curve_from_panel(((raw0 or {}).get("panels") or {}).get("mana_curve"))
    t_cmc = blend(cmc_distribution(avg_nonland) if avg_nonland else None, panel_curve, C.CMC_BUCKETS)
    t_cmc = apply_efficiency_floor(t_cmc)
    t_type = blend(type_distribution(avg_nonland) if avg_nonland else None, types_from_page(raw0), C.CARD_TYPES)
    u_cmc = cmc_distribution(deck_nonland)
    u_type = type_distribution(deck_nonland)
    d_type = {t: (u_type[t] - t_type[t]) / max(t_type[t], 0.05) for t in C.CARD_TYPES}
    roles = {r: 0 for r in C.ROLES}
    for m in deck_nonland:
        for r in m.roles:
            if r in roles:
                roles[r] += 1
    cmc_counts = {b: 0 for b in C.CMC_BUCKETS}
    type_counts = {t: 0.0 for t in C.CARD_TYPES}
    for m in deck_nonland:
        cmc_counts[m.cmc_bucket] += 1
        for t, w in type_weights(m).items():
            type_counts[t] += w
    return StructuralModel(
        target_cmc=t_cmc,
        target_type=t_type,
        user_cmc=u_cmc,
        user_type=u_type,
        delta_cmc=curve_delta(u_cmc, t_cmc),
        delta_type=d_type,
        role_counts=roles,
        land_count=len(deck_lands),
        mdfc_count=sum(1 for m in deck_nonland if m.is_mdfc_land),
        nonland_count=len(deck_nonland),
        user_cmc_counts=cmc_counts,
        user_type_counts=type_counts,
        role_floors=role_floors_from_avg(avg_nonland),
    )


def status_label(user: float, target: float, tolerance: float = 0.15) -> str:
    if target <= 0:
        return "BLOATED" if user > 0 else "OPTIMAL"
    rel = (user - target) / target
    if rel > tolerance:
        return "BLOATED"
    if rel < -tolerance:
        return "STARVED"
    return "OPTIMAL"
