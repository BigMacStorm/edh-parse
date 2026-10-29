"""Tunable constants for the deck evaluator (weights, thresholds, budgets, staples)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Tuple

# --------------------------------------------------------------------------- network
EDHREC_JSON_BASE = "https://json.edhrec.com/pages"
EDHREC_SITE_BASE = "https://edhrec.com"
SCRYFALL_SEARCH_URL = "https://api.scryfall.com/cards/search"
USER_AGENT = "EDHFiveVectorOptimizer/2.0 (contact: local-cli)"

EDHREC_DELAY_RANGE = (1.8, 3.2)  # seconds between consecutive uncached EDHREC requests (json.edhrec.com CDN)
# edhrec.com itself serves ~1.2 MB server-rendered pages and answers a burst with an HTTP 200
# "429 Too Many Requests" page, so the website host gets a slower pace.
EDHREC_WEB_DELAY_RANGE = (4.0, 7.0)
SCRYFALL_MIN_INTERVAL = 0.12  # seconds between Scryfall requests
BACKOFF_SCHEDULE = (5, 10, 20)  # 429 / 503 retries
DEFAULT_MAX_REQUESTS = 22
DEFAULT_CACHE_TTL_DAYS = 7
# Request slots S1 tuple/theme fetches must leave untouched so S3 hub pages are never starved.
MIN_HUB_BUDGET_RESERVE = 3
ROLE_TAG_TTL_DAYS = 30  # Scryfall oracle tags change slowly
SCRYFALL_QUERY_NAME_CHARS = 850  # Scryfall silently truncates q at ~1000 chars

# --------------------------------------------------------------------------- statistics
EPS = 0.015  # rate stabiliser used in every ratio
PRIOR_RATE = 0.015  # p_0 for empirical-Bayes smoothing
PRIOR_STRENGTH = 25.0  # alpha
MIN_EXPECTED_RATE = 0.005
# EDHREC only lists cards above an inclusion floor (~5%). A card missing from a page is
# "somewhere under the floor", not 0%; we estimate it at this fraction of the page floor.
CENSORED_FRACTION = 0.5
# A rare card on a page's inclusion floor can imply a huge multiplicative lift (p / ~0); beyond
# this the number is noise and would dominate every lift-weighted ranking.
MAX_LIFT = 15.0

# --------------------------------------------------------------------------- triage
STAPLE_INCLUSION = 0.48
STAPLE_MAX_LIFT = 1.35
ANCHOR_MIN_INCLUSION = 0.03
SIGNATURE_LIFT = 1.35

FORMAT_STAPLES: FrozenSet[str] = frozenset(
    s.lower()
    for s in (
        "Sol Ring", "Arcane Signet", "Fellwar Stone", "Mind Stone", "Thought Vessel",
        "Wayfarer's Bauble", "Swords to Plowshares", "Path to Exile", "Pongify",
        "Rapid Hybridization", "Beast Within", "Generous Gift", "Stroke of Midnight",
        "Anguished Unmaking", "Assassin's Trophy", "Chaos Warp", "Counterspell", "Swan Song",
        "Arcane Denial", "An Offer You Can't Refuse", "Dovin's Veto", "Fierce Guardianship",
        "Deflecting Swat", "Deadly Rollick", "Flawless Maneuver", "Teferi's Protection",
        "Heroic Intervention", "Blasphemous Act", "Toxic Deluge", "Farewell", "Cyclonic Rift",
        "Vandalblast", "Cultivate", "Kodama's Reach", "Rampant Growth", "Farseek",
        "Nature's Lore", "Three Visits", "Birds of Paradise", "Delighted Halfling",
        "Llanowar Elves", "Elvish Mystic", "Elves of Deep Shadows", "Avacyn's Pilgrim",
        "Ignoble Hierarch", "Noble Hierarch", "Deathrite Shaman", "Demonic Tutor",
        "Vampiric Tutor", "Imperial Seal", "Enlightened Tutor", "Worldly Tutor",
        "Mystical Tutor", "Gamble", "Rhystic Study", "Mystic Remora", "Esper Sentinel",
        "Smothering Tithe", "Trouble in Pairs", "The One Ring", "Sylvan Library",
        "Phyrexian Arena", "Black Market Connections", "Jeska's Will", "Dark Ritual",
        "Cabal Ritual", "Mana Drain", "Force of Will", "Force of Negation", "Pact of Negation",
        "Silence", "Grand Abolisher",
    )
)

# --------------------------------------------------------------------------- algorithm 1
DEFAULT_TUPLES = 8
TUPLE_SIZE = 2
TRAP_BAGLIFT = 0.88
TRAP_PENALTY_WEIGHT = 1.75
PRECON_LOW_MIN = 0.25
PRECON_HIGH_MAX = 0.06
PRECON_EXCLUSIONS = 2
MAX_THEME_PAGES = 4

# --------------------------------------------------------------------------- algorithm 2
CHEAP_PRICE = 10.0
DEFAULT_PRICE = 1.00
CLUNKY_CMC = 4
CLUNKY_PHI = 1.30
# Price tiers confound "optimised" with "expensive": decks in the top price decile swap cheap
# cards for fast mana because they can, not because the cheap card is bad. The goal of S2 is
# to reward cheap cards that survive in expensive decks, so the penalty side is damped.
NEGATIVE_SURVIVOR_DAMPING = 0.5
DEFAULT_MAX_ADD_BUDGET = 25.0

# --------------------------------------------------------------------------- algorithm 3
DEFAULT_HUBS = 5
HUB_PRESENCE = 0.06
CONVERGENCE_STEP = 0.40
HUB_ENGINE_BAGLIFT = 1.25

# --------------------------------------------------------------------------- algorithm 4
CMC_BUCKETS: Tuple[str, ...] = ("0-1", "2", "3", "4", "5", "6+")
CARD_TYPES: Tuple[str, ...] = ("Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker")
ROLES: Tuple[str, ...] = ("RAMP", "DRAW", "REMOVAL", "BOARD_WIPE")
ROLE_FLOORS: Dict[str, int] = {"RAMP": 10, "DRAW": 10, "REMOVAL": 8, "BOARD_WIPE": 2}
# When the average deck is role-tagged its counts (blended with ROLE_FLOORS by AVG_DECK_BLEND) set
# the floors, kept inside these safety bounds so an odd average deck can't license 3 ramp or 25.
ROLE_FLOOR_BOUNDS: Dict[str, Tuple[int, int]] = {
    "RAMP": (6, 14), "DRAW": (7, 14), "REMOVAL": (5, 12), "BOARD_WIPE": (1, 4),
}
EFFICIENCY_FLOOR = 0.36
AVG_DECK_BLEND = 0.70
LOW_CMC_BLOAT_FACTOR = 0.35
HIGH_CMC_BLOAT_FACTOR = 1.45
HIGH_CMC_STARVE_CLAMP = -0.20
ROLE_BOOST_WEIGHT = 0.60
MDFC_LAND_BOOST = 0.15
MDFC_LAND_TARGET = 38

# Scryfall oracle-tag queries per functional role (see tests / README for how they were picked).
ROLE_TAG_QUERIES: Dict[str, str] = {
    "RAMP": "otag:ramp",
    "DRAW": "otag:card-advantage -otag:cantrip",
    "REMOVAL": "(otag:removal or otag:counterspell) -otag:sweeper",
    "BOARD_WIPE": "otag:sweeper",
}
RAMP_MAX_CMC = 4

# --------------------------------------------------------------------------- algorithm 5
DEFAULT_WEIGHTS: Tuple[float, float, float, float] = (0.38, 0.24, 0.23, 0.15)
SECRET_TECH_WEIGHTS: Tuple[float, float, float, float] = (0.48, 0.04, 0.38, 0.10)
THEME_FALLBACK_S1_FACTOR = 0.5  # S1 weight multiplier when only theme pages were available
TANH_GAIN = 0.55
# Smallest in-deck spread (per signal S1..S4) the z-score may divide by. Without it a deck whose
# S2/S3/S4 are nearly constant makes a trivial raw difference project to +/-100. Starting values,
# not fitted: re-check against the backtest before tuning.
MIN_SIGNAL_SCALES: Tuple[float, float, float, float] = (0.015, 0.020, 0.015, 0.050)
# Swap-gain adjustments. Their sum is kept at or below 20 points so that "same shape" can never
# outvote a clearly better card (the swap bar on the S5 difference itself is MIN_SWAP_DELTA = 12).
SWAP_CURVE_BONUS = 6.0  # add costs the same or less mana than the cut
SWAP_CURVE_PENALTY_PER_CMC = 4.0
SWAP_TYPE_BONUS = 8.0
SWAP_ROLE_BONUS = 6.0
SWAP_SCARCE_ROLE_PENALTY = 200.0  # effectively a veto
FINALISTS = 75
CUT_POOL = 15
ADD_POOL = 30
MAX_SWAPS = 10
MIN_SWAP_GAIN = 15.0
MIN_SWAP_DELTA = 12.0
REPORT_CUTS = 15
REPORT_ADDS = 20
LAND_UPGRADES = 3


@dataclass
class EvaluateOptions:
    commander: str = ""
    max_add_budget: float = DEFAULT_MAX_ADD_BUDGET
    tuples: int = DEFAULT_TUPLES
    hubs: int = DEFAULT_HUBS
    max_requests: int = DEFAULT_MAX_REQUESTS
    cache_ttl_days: float = DEFAULT_CACHE_TTL_DAYS
    fresh_cache: bool = False
    weights: Tuple[float, float, float, float] = field(default=DEFAULT_WEIGHTS)
