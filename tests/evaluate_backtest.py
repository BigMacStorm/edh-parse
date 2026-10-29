"""Offline backtest harness for the deck evaluator.

Builds a seeded synthetic commander world with *known ground truth* (which cards belong to the
archetype, which are traps that look popular on the baseline page but vanish from the build-path
cohorts) and measures how well ``Evaluator`` ranks them:

* hold-out recall@K  - remove archetype cards from the deck; how many come back as top adds?
* trap cut recall@K / MRR - how highly do the trap cards rank among the reported cuts?

This only exercises the scoring mechanics against a world we control. It says nothing about
real-world recommendation quality until it is fed recorded EDHREC pages.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

from edhparse.evaluate import config as C
from edhparse.evaluate.deck_parser import parse_decklist_text
from edhparse.evaluate.edhrec import edhrec_slug
from edhparse.evaluate.engine import Evaluator
from edhparse.evaluate.scryfall_meta import CardMetaLookup
from tests.test_evaluate import FakeResp, FakeSession, filtered_html, make_fetcher

SLUG = "backtest-commander"
N_D0 = 2000
N_FILTER = 400
N_TIER = 200
N_NOISY = 12


@dataclass
class World:
    session: FakeSession
    lookup: object
    deck_text: str
    archetype: List[str]
    holdout: List[str]
    traps: List[str]
    roles: Dict[str, set] = field(default_factory=dict)


class _MapTagger:
    failed = False

    def __init__(self, roles: Dict[str, set]):
        self.roles = roles

    def tag(self, metas):
        for m in metas:
            m.roles = set(self.roles.get(m.name, ()))


def build_world(seed: int, n_archetype: int = 60, n_holdout: int = 6, n_traps: int = 6, n_in_deck_arch: int = 34) -> World:
    rng = random.Random(seed)
    base = C.EDHREC_JSON_BASE
    archetype = [f"Arch {i:02d}" for i in range(n_archetype)]
    rng.shuffle(archetype)
    holdout = archetype[:n_holdout]
    in_deck_arch = archetype[n_holdout:n_holdout + n_in_deck_arch]
    traps = [f"Trap {i:02d}" for i in range(n_traps)]

    roles = {name: {"RAMP"} for i, name in enumerate(archetype) if i % 7 == 0}
    types = {}
    for i, name in enumerate(archetype):
        types[name] = ("Creature — Elf" if i % 2 else "Enchantment", 2 + i % 3)
    for name in traps:
        types[name] = ("Creature — Giant", 5)

    def jitter(x: float) -> float:
        return max(0.02, x * rng.uniform(0.9, 1.1))

    # Baseline page (D0): archetype cards are common and synergistic; traps look equally popular.
    d0: Dict[str, Tuple[int, float]] = {}
    for rank, name in enumerate(sorted(archetype)):
        d0[name] = (int(N_D0 * jitter(0.55 - 0.0065 * rank)), 0.20)
    for name in traps:
        d0[name] = (int(N_D0 * jitter(0.24)), 0.08)

    def page(n: int, cards: Dict[str, Tuple[int, float]]) -> str:
        return json.dumps({
            "creature": 30, "instant": 10, "sorcery": 10, "artifact": 10, "enchantment": 10, "planeswalker": 0,
            "panels": {"mana_curve": {"1": 10, "2": 20, "3": 15, "4": 8, "5": 4, "6": 3},
                       "taglinks": [{"slug": "elves", "count": 300}]},
            "container": {"json_dict": {"card": {"num_decks": n}, "cardlists": [{"tag": "cards", "cardviews": [
                {"name": k, "sanitized": edhrec_slug(k), "num_decks": v[0], "potential_decks": n, "synergy": v[1]}
                for k, v in cards.items()]}]}}})

    def scaled(n: int) -> Dict[str, Tuple[int, float]]:
        return {k: (max(1, int(v[0] * n / N_D0)), v[1]) for k, v in d0.items()}

    routes = {
        f"{base}/commanders/{SLUG}.json": FakeResp(200, page(N_D0, d0)),
        f"{base}/commanders/{SLUG}/expensive.json": FakeResp(200, page(N_TIER, scaled(N_TIER))),
        f"{base}/commanders/{SLUG}/budget.json": FakeResp(200, page(N_TIER, scaled(N_TIER))),
        f"{base}/average-decks/{SLUG}/expensive.json": FakeResp(
            200, json.dumps({"deck": {"cards": {"x": [[n, 1] for n in in_deck_arch]}}})),
    }

    # Build-path cohort: archetype cards concentrate, traps collapse to the listing floor.
    filtered = {n: (int(N_FILTER * jitter(0.70 - 0.005 * i)), 0.30) for i, n in enumerate(sorted(archetype))}
    filtered.update({n: (int(N_FILTER * 0.06), -0.20) for n in traps})
    html = filtered_html(N_FILTER, filtered, commander=SLUG)
    # A third of the build paths are tiny, noisy cohorts that wrongly make the traps look popular.
    noisy = {n: (rng.randint(3, 9), 0.0) for n in archetype}
    noisy.update({n: (rng.randint(9, 11), 0.0) for n in traps})
    noisy_html = filtered_html(N_NOISY, noisy, commander=SLUG)

    class WorldSession(FakeSession):
        def get(self, url, params=None, only_if_cached=False, **kw):
            if only_if_cached:
                return FakeResp(504)
            self.network_calls.append(url)
            if "edhrec.com/commanders/" in url and "?f=" in url:
                return FakeResp(200, noisy_html if sum(map(ord, url)) % 3 == 0 else html, "text/html")
            if "/pages/cards/" in url:
                return FakeResp(403, "<Error><Code>AccessDenied</Code></Error>", "application/xml")
            return routes.get(url) or FakeResp(403, "<Error><Code>AccessDenied</Code></Error>", "application/xml")

    lookup_cards = {}
    for name, (tl, cmc) in types.items():
        lookup_cards[name.lower()] = {"name": name, "layout": "normal", "cmc": cmc, "type_line": tl,
                                      "color_identity": ["G"], "prices": {"usd": "1.0"},
                                      "legalities": {"commander": "legal"}, "oracle_text": ""}
    lookup_cards["forest"] = {"name": "Forest", "layout": "normal", "cmc": 0, "type_line": "Basic Land — Forest",
                              "color_identity": ["G"], "prices": {"usd": "0.1"}, "legalities": {"commander": "legal"}}
    lookup_cards["backtest commander"] = {
        "name": "Backtest Commander", "layout": "normal", "cmc": 4, "type_line": "Legendary Creature — Elf",
        "color_identity": ["G"], "prices": {"usd": "1"}, "legalities": {"commander": "legal"}}

    deck_text = ("Commander\n1 Backtest Commander\n\nDeck\n"
                 + "".join(f"1 {n}\n" for n in in_deck_arch + traps) + "30 Forest\n")
    return World(WorldSession(), (lambda n: lookup_cards.get(n.lower())), deck_text, archetype, holdout, traps, roles)


# --------------------------------------------------------------------------- metrics


def recall_at_k(ranked: Sequence[str], targets: Sequence[str], k: int) -> float:
    if not targets:
        return 0.0
    return len(set(ranked[:k]) & set(targets)) / len(targets)


def reciprocal_ranks(ranked: Sequence[str], targets: Sequence[str]) -> List[float]:
    pos = {n: i + 1 for i, n in enumerate(ranked)}
    return [1.0 / pos[t] if t in pos else 0.0 for t in targets]


def mean_reciprocal_rank(ranked: Sequence[str], targets: Sequence[str]) -> float:
    rr = reciprocal_ranks(ranked, targets)
    return sum(rr) / len(rr) if rr else 0.0


def run_world(seed: int, **world_kw) -> Dict[str, float]:
    w = build_world(seed, **world_kw)
    fetcher = make_fetcher(w.session)
    ev = Evaluator(w.session, C.EvaluateOptions(tuples=6, hubs=3), fetcher=fetcher, metas=CardMetaLookup(w.lookup),
                   role_tagger=_MapTagger(w.roles), log=lambda m: None)
    res = ev.run(parse_decklist_text(w.deck_text))
    adds = [c.name for c in res.adds]
    cuts = [c.name for c in res.cuts]
    swap_cuts = [p.cut.name for p in res.swaps]
    swap_adds = [p.add.name for p in res.swaps]
    return {
        "holdout_recall_at_10": recall_at_k(adds, w.holdout, 10),
        "holdout_recall_at_20": recall_at_k(adds, w.holdout, 20),
        "trap_cut_recall_at_6": recall_at_k(cuts, w.traps, 6),
        "trap_cut_mrr": mean_reciprocal_rank(cuts, w.traps),
        "swap_trap_recall": recall_at_k(swap_cuts, w.traps, len(swap_cuts)),
        "swap_holdout_recall": recall_at_k(swap_adds, w.holdout, len(swap_adds)),
        "requests": float(fetcher.budget.used),
    }


def run_suite(seeds: Sequence[int] = tuple(range(1, 13)), **world_kw) -> Dict[str, float]:
    runs = [run_world(s, **world_kw) for s in seeds]
    return {k: round(sum(r[k] for r in runs) / len(runs), 4) for k in runs[0]}


if __name__ == "__main__":  # python -m tests.evaluate_backtest
    print(json.dumps(run_suite(), indent=2))


def fingerprint(res) -> Dict[str, object]:
    """Every score, tag and recommendation of a result, for characterization tests."""
    def row(c):
        return [c.name, c.s5, [round(x, 1) for x in c.s], sorted(c.tags)]
    return {
        "deck": sorted(row(c) for c in res.deck_scores),
        "candidates": sorted(row(c) for c in res.candidate_scores),
        "cuts": [c.name for c in res.cuts],
        "adds": [c.name for c in res.adds],
        "swaps": [[p.cut.name, p.add.name, p.gain, p.delta] for p in res.swaps],
        "lands": [[u.cut, u.add] for u in res.land_upgrades],
        "weights": [round(w, 4) for w in res.weights],
        "warnings": sorted(res.warnings),
    }


def evaluate_world(seed: int, **world_kw):
    w = build_world(seed, **world_kw)
    ev = Evaluator(w.session, C.EvaluateOptions(tuples=6, hubs=3), fetcher=make_fetcher(w.session),
                   metas=CardMetaLookup(w.lookup), role_tagger=_MapTagger(w.roles), log=lambda m: None)
    return ev.run(parse_decklist_text(w.deck_text))
