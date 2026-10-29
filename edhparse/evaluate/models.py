"""Data models for the deck evaluator (dataclasses; no extra dependencies)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from edhparse.evaluate import config as C


def name_key(name: str) -> str:
    """Case-insensitive lookup key for card names."""
    return (name or "").strip().lower()


def front_face(name: str) -> str:
    return (name or "").split(" // ", 1)[0].strip()


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def lift_from_synergy(p_raw: float, synergy: float) -> float:
    """EDHREC ``synergy`` is additive (P_raw - P_expected). Rebuild a multiplicative lift."""
    s = float(synergy or 0.0)
    if abs(s) > 1.0:  # reported on a [-100, 100] scale
        s /= 100.0
    p_expected = max(C.MIN_EXPECTED_RATE, p_raw - s)
    return min(C.MAX_LIFT, (p_raw + C.EPS) / (p_expected + C.EPS))


@dataclass
class CardStat:
    """One card's numbers on one EDHREC page."""

    name: str
    slug: str = ""
    num_decks: int = 0  # n_c
    potential_decks: int = 0  # N_c
    synergy: Optional[float] = None
    lift: Optional[float] = None  # given directly by hub / filtered pages
    category: str = ""


@dataclass
class Cohort:
    """A population of decks (commander page, price tier, filtered cohort, card hub...)."""

    label: str
    url: str = ""
    num_decks: int = 0  # N
    cards: Dict[str, CardStat] = field(default_factory=dict)  # keyed by name_key
    source: str = "json"  # json | html-filter | theme | merged | hub
    raw: Optional[dict] = None  # original JSON (structural panels, average-deck list)

    # -- membership ------------------------------------------------------------
    def add(self, stat: CardStat) -> None:
        key = name_key(stat.name)
        if key in self.cards:
            return
        self.__dict__.pop("_floor_cache", None)
        self.cards[key] = stat
        ff = name_key(front_face(stat.name))
        if ff != key and ff not in self.cards:
            self.cards[ff] = stat

    def get(self, name: str) -> Optional[CardStat]:
        stat = self.cards.get(name_key(name))
        if stat is None:
            stat = self.cards.get(name_key(front_face(name)))
        return stat

    def present(self, name: str) -> bool:
        return self.get(name) is not None

    def names(self) -> List[str]:
        seen: Set[int] = set()
        out = []
        for stat in self.cards.values():
            if id(stat) in seen:
                continue
            seen.add(id(stat))
            out.append(stat.name)
        return out

    # -- rates -----------------------------------------------------------------
    def _denominator(self, stat: Optional[CardStat]) -> int:
        if stat is not None and stat.potential_decks and stat.potential_decks > 0:
            return int(stat.potential_decks)
        return max(int(self.num_decks or 0), 0)

    @property
    def floor_rate(self) -> float:
        """Smallest listed inclusion among cards eligible in most decks (the page's cut-off)."""
        cached = getattr(self, "_floor_cache", None)
        if cached is not None:
            return cached
        rates = []
        for stat in {id(s): s for s in self.cards.values()}.values():
            denom = self._denominator(stat)
            if denom <= 0 or stat.num_decks <= 0:
                continue
            if self.num_decks and denom < 0.5 * self.num_decks:
                continue  # new cards with few potential decks distort the floor
            rates.append(stat.num_decks / denom)
        floor = min(rates) if rates else 0.0
        object.__setattr__(self, "_floor_cache", floor)
        return floor

    def p_raw(self, name: str) -> float:
        """Raw inclusion rate (0.0 when the card is not listed, as the spec defines it)."""
        stat = self.get(name)
        if stat is None:
            return 0.0
        denom = self._denominator(stat)
        if denom <= 0:
            return 0.0
        return clamp(stat.num_decks / denom, 0.0, 1.0)

    def p_est(self, name: str) -> float:
        """Inclusion estimate that treats unlisted cards as censored below the page floor."""
        if self.present(name):
            return self.p_raw(name)
        return C.CENSORED_FRACTION * self.floor_rate

    def p_smooth(self, name: str) -> float:
        """Empirical-Bayes smoothed inclusion (P_smooth)."""
        stat = self.get(name)
        if stat is not None:
            denom = self._denominator(stat)
            n = float(stat.num_decks)
        else:
            denom = max(int(self.num_decks or 0), 0)
            n = self.p_est(name) * denom
        return (n + C.PRIOR_STRENGTH * C.PRIOR_RATE) / (denom + C.PRIOR_STRENGTH)

    def lift(self, name: str) -> float:
        """Multiplicative lift on this page (1.0 = neutral; unlisted cards are neutral)."""
        stat = self.get(name)
        if stat is None:
            return 1.0
        if stat.synergy is not None:
            return lift_from_synergy(self.p_raw(name), stat.synergy)
        if stat.lift is not None and stat.lift > 0:
            return min(C.MAX_LIFT, float(stat.lift))
        return 1.0

    def top_cards(self, k: int = 3) -> List[Tuple[str, int]]:
        """Most-played cards; ties broken by name so JSON- and HTML-parsed pages compare equal."""
        uniq = {id(s): s for s in self.cards.values()}.values()
        ranked = sorted(uniq, key=lambda s: (-s.num_decks, s.name))
        return [(s.name, s.num_decks) for s in ranked[:k]]


@dataclass
class CardMeta:
    """Scryfall-derived metadata, normalised for double-faced / split cards."""

    name: str  # Scryfall canonical name
    front: str
    cmc: float = 0.0
    color_identity: Tuple[str, ...] = ()
    type_line: str = ""  # front face for MDFCs, full otherwise
    oracle_text: str = ""  # all faces joined
    price: float = C.DEFAULT_PRICE
    price_known: bool = False
    layout: str = "normal"
    is_land: bool = False
    is_mdfc_land: bool = False
    legal: bool = True
    found: bool = True
    roles: Set[str] = field(default_factory=set)

    def within_budget(self, budget: float) -> bool:
        """Only cards with a verified price can be recommended under a budget."""
        return self.price_known and self.price <= budget

    @property
    def is_basic(self) -> bool:
        return self.is_land and "Basic" in self.type_line

    @property
    def is_creature(self) -> bool:
        return "Creature" in self.type_line

    @property
    def is_legendary_creature(self) -> bool:
        return "Legendary" in self.type_line and "Creature" in self.type_line

    @property
    def types(self) -> List[str]:
        main = self.type_line.split("—", 1)[0]
        return [t for t in C.CARD_TYPES if t in main]

    @property
    def primary_type(self) -> str:
        types = self.types
        if not types:
            return "Land" if self.is_land else "Other"
        if "Creature" in types:
            return "Creature"
        return types[0]

    @property
    def cmc_bucket(self) -> str:
        v = self.cmc
        if v <= 1:
            return "0-1"
        if v >= 6:
            return "6+"
        return str(int(v))


@dataclass
class DeckEntry:
    name: str
    quantity: int = 1
    protected: bool = False
    tags: List[str] = field(default_factory=list)


@dataclass
class Decklist:
    commanders: List[str]
    cards: List[DeckEntry]
    warnings: List[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.commanders) + sum(e.quantity for e in self.cards)

    def protected_names(self) -> Set[str]:
        return {name_key(e.name) for e in self.cards if e.protected}


@dataclass
class CardScores:
    name: str
    in_deck: bool
    meta: CardMeta
    pool: str = ""  # L / U / A / Q / X (external)
    p_raw_d0: float = 0.0
    lift_d0: float = 1.0
    e_context: float = 0.0
    bag_lift: float = 1.0
    orr: float = 1.0
    k_hubs: int = 0
    slot_pressure: float = 0.0
    role_boost: float = 0.0
    raw: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    s: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    s5: float = 0.0
    tags: List[str] = field(default_factory=list)
    protected: bool = False
    requires_same_role_replacement: bool = False
    scarce_roles: Set[str] = field(default_factory=set)
    finalist: bool = True
    prelim_score: float = 0.0  # S1-S3 pre-screen score used to pick finalists


@dataclass
class SwapPair:
    cut: CardScores
    add: CardScores
    gain: float
    delta: float
    rationale: str


@dataclass
class LandUpgrade:
    cut: str
    cut_score: float
    add: str
    add_score: float
    price: float
