"""Algorithm 3 (S3): "secret commander" hub webbing.

The deck's strongest non-legendary engines are looked up on their own EDHREC card pages
(across every commander), surfacing packages of cards that travel with those engines.
Card pages report ``lift`` directly (they carry no ``synergy`` field).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Sequence, Tuple

from edhparse.evaluate import config as C
from edhparse.evaluate.models import CardMeta, Cohort, name_key


@dataclass
class HubScore:
    k_hubs: int
    raw: float


def select_hubs(anchors: Sequence[str], metas: Dict[str, CardMeta], d0: Cohort,
                bag: Callable[[str], Tuple[float, float]], h: int = C.DEFAULT_HUBS) -> List[str]:
    """Rank anchors by E * ln(max(1.15, BagLift)) * Lift(D0); skip one-shot spells and
    legendary creatures (unless they are clear engines)."""
    scored = []
    for name in anchors:
        meta = metas[name_key(name)]
        e, bag_lift = bag(name)
        types = meta.types
        if ("Instant" in types or "Sorcery" in types) and not ({"Creature", "Artifact", "Enchantment", "Planeswalker"} & set(types)):
            continue  # one-shot effects (cantrips, tutors, removal) are not engines
        if meta.is_legendary_creature and bag_lift < C.HUB_ENGINE_BAGLIFT:
            continue
        scored.append((e * math.log(max(1.15, bag_lift)) * d0.lift(name), name))
    scored.sort(reverse=True)
    return [n for _, n in scored[:h]]


def hub_score(name: str, hubs: Sequence[Tuple[str, Cohort]]) -> HubScore:
    key = name_key(name)
    total = 0.0
    count = 0
    k = 0
    for hub_name, hub in hubs:
        if hub is None or name_key(hub_name) == key:
            continue
        count += 1
        p = hub.p_raw(name)
        lift = hub.lift(name) if hub.present(name) else 1.0
        if p >= C.HUB_PRESENCE:
            k += 1
        total += p * (lift ** 1.2)
    if count == 0:
        return HubScore(k_hubs=0, raw=0.0)
    boost = 1.0 + C.CONVERGENCE_STEP * max(0, k - 1)
    return HubScore(k_hubs=k, raw=boost * total / count)
