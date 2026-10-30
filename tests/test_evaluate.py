"""Tests for the five-score deck evaluator (no network: fake sessions + recorded EDHREC fixtures)."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

import pytest

from edhparse.evaluate import config as C
from edhparse.evaluate.algorithms import anchor_bagging as A1
from edhparse.evaluate.algorithms import ensemble as A5
from edhparse.evaluate.algorithms import hub_webbing as A3
from edhparse.evaluate.algorithms import structural_fit as A4
from edhparse.evaluate.algorithms import survivor_diff as A2
from edhparse.evaluate.deck_parser import clean_card_line, parse_decklist_text
from edhparse.evaluate.edhrec import (
    EdhrecFetcher,
    commander_slug,
    edhrec_slug,
    filtered_page_url,
    parse_filtered_html,
    parse_json_page,
)
from edhparse.evaluate.engine import Evaluator
from edhparse.evaluate.models import CardMeta, CardScores, CardStat, Cohort, lift_from_synergy
from edhparse.evaluate.scryfall_meta import (
    CardMetaLookup,
    RoleTagCache,
    RoleTagger,
    build_role_query,
    chunk_names,
    meta_from_scryfall,
)

FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- helpers


def cohort(label: str, n: int, cards: Dict[str, tuple], source: str = "json") -> Cohort:
    """cards: name -> (num_decks, synergy, lift, potential_decks)"""
    c = Cohort(label=label, num_decks=n, source=source)
    for name, spec in cards.items():
        spec = tuple(spec) + (None,) * (4 - len(spec))
        nd, syn, lift, pot = spec
        c.add(CardStat(name=name, slug=edhrec_slug(name), num_decks=int(nd), potential_decks=int(pot or n),
                       synergy=syn, lift=lift))
    return c


def meta(name: str, cmc: float = 2, type_line: str = "Creature — Elf", price: float = 1.0, roles=(), ci=("G",),
         land: bool = False, mdfc_land: bool = False, known: bool = True) -> CardMeta:
    return CardMeta(name=name, front=name.split(" // ")[0], cmc=cmc, color_identity=tuple(ci), type_line=type_line,
                    price=price, price_known=known, is_land=land, is_mdfc_land=mdfc_land, roles=set(roles))


def scores(name: str, in_deck: bool, m: Optional[CardMeta] = None, raw=(0.0, 0.0, 0.0, 0.0), **kw) -> CardScores:
    cs = CardScores(name=name, in_deck=in_deck, meta=m or meta(name), **kw)
    cs.raw = list(raw)
    return cs


class FakeResp:
    def __init__(self, status=200, text="", ctype="application/json", headers=None, from_cache=False, age_days=0.0):
        self.status_code = status
        self.text = text
        self.headers = {"Content-Type": ctype, **(headers or {})}
        self.from_cache = from_cache
        self.created_at = datetime.now(timezone.utc) - timedelta(days=age_days)

    def json(self):
        return json.loads(self.text)


class FakeSession:
    """Serves canned responses; ``cache`` holds URLs that requests-cache would already have."""

    def __init__(self, routes=None, cache=None):
        self.routes = routes or {}
        self.cache = cache or {}
        self.network_calls = []

    def get(self, url, params=None, only_if_cached=False, force_refresh=False, **kw):
        if params:
            from urllib.parse import urlencode

            url = url + "?" + urlencode(params)
        if only_if_cached:
            if url in self.cache:
                return self.cache[url]
            return FakeResp(504)
        self.network_calls.append(url)
        route = self.routes.get(url)
        if callable(route):
            return route(url)
        if isinstance(route, list):
            return route.pop(0)
        return route or FakeResp(404, "not found", "text/plain")


def no_sleep(_s):
    pass


def make_fetcher(session, **kw):
    return EdhrecFetcher(session, sleep=no_sleep, rand=lambda a, b: 0.0, missing_cache_path=None, **kw)


def filtered_html(n_total: int, cards: Dict[str, tuple], commander="korvold-fae-cursed-king") -> str:
    recs = [{"category": "$undefined", "numDecks": n_total, "potentialDecks": "$undefined", "role": "commander",
             "cardId": commander, "name": "Commander"}]
    for name, (nd, syn) in cards.items():
        recs.append({"category": "creatures", "numDecks": nd, "potentialDecks": n_total, "synergy": syn,
                     "lift": "$undefined", "role": "$undefined", "cardId": edhrec_slug(name), "name": name})
    flight = '0:{"recs":' + json.dumps(recs, separators=(",", ":")) + "}"
    return "<html><script>self.__next_f.push([1," + json.dumps(flight) + "])</script></html>"


# =========================================================================== spec invariant 1


def test_synergy_to_lift_reconstruction():
    # +0.30 synergy on 0.50 inclusion: expected rate 0.20 -> lift > 1
    assert lift_from_synergy(0.50, 0.30) == pytest.approx((0.50 + 0.015) / (0.20 + 0.015))
    assert lift_from_synergy(0.50, 0.30) > 2.0
    # negative synergy -> lift < 1 (no max(1.0, synergy) flatline)
    assert lift_from_synergy(0.10, -0.20) < 1.0
    assert lift_from_synergy(0.25, 0.0) == pytest.approx(1.0)
    # percentage-scaled synergy is accepted
    assert lift_from_synergy(0.50, 30.0) == pytest.approx(lift_from_synergy(0.50, 0.30))
    # Matches EDHREC's own published lift for Mayhem Devil on a filtered page (fixture)
    c = parse_filtered_html((FIXTURES / "edhrec_filtered_korvold.html").read_text(), "fx")
    stat = c.get("Mayhem Devil")
    p = stat.num_decks / stat.potential_decks
    exact = p / (p - stat.synergy)
    assert exact == pytest.approx(stat.lift, rel=1e-6)
    assert c.lift("Mayhem Devil") > 2.5
    # Unlisted cards are neutral
    assert c.lift("Not On The Page") == 1.0


# =========================================================================== spec invariant 2


def _theme_json(n, cards):
    return json.dumps({"container": {"json_dict": {"card": {"num_decks": n}, "cardlists": [
        {"tag": "creatures", "cardviews": [
            {"name": k, "sanitized": edhrec_slug(k), "num_decks": v, "potential_decks": n, "synergy": 0.1}
            for k, v in cards.items()]}]}}})


def test_cdn_query_stripping_idempotency_fallback():
    slug = "korvold-fae-cursed-king"
    base_cards = {"Alpha": (600, 0.2), "Beta": (500, 0.2), "Gamma": (300, 0.1), "Delta": (200, 0.1), "Zeta": (100, 0.0)}
    d0 = cohort("D0", 1000, {k: (v[0], v[1]) for k, v in base_cards.items()})
    d0.raw = {"panels": {"taglinks": [{"slug": "tokens", "count": 300, "value": "Tokens"},
                                      {"slug": "sacrifice", "count": 250, "value": "Sacrifice"}]}}
    # The "filtered" route returns the unfiltered page: same N and same top cards.
    same_page = filtered_html(1000, base_cards)
    routes = {}
    pairs = [("Alpha", "Beta"), ("Gamma", "Delta")]
    for pair in pairs:
        routes[filtered_page_url(slug, [edhrec_slug(a) for a in pair])] = FakeResp(200, same_page, "text/html")
    routes[f"{C.EDHREC_JSON_BASE}/commanders/{slug}/tokens.json"] = FakeResp(
        200, _theme_json(300, {"Alpha": 290, "Beta": 280, "Gamma": 30, "Delta": 20, "Zeta": 150}))
    routes[f"{C.EDHREC_JSON_BASE}/commanders/{slug}/sacrifice.json"] = FakeResp(
        200, _theme_json(250, {"Alpha": 20, "Beta": 30, "Gamma": 240, "Delta": 230, "Zeta": 5}))
    session = FakeSession(routes)
    fetcher = make_fetcher(session)
    ev = Evaluator(session, C.EvaluateOptions(), fetcher=fetcher, metas=CardMetaLookup(lambda n: None),
                   role_tagger=None, log=lambda m: None)
    tuples = ev._tuple_cohorts(slug, pairs, [], d0, ["Alpha", "Beta", "Gamma", "Delta"])

    assert fetcher.telemetry.static_cdn_ignores_query_params is True
    filter_calls = [u for u in session.network_calls if "edhrec.com/commanders/" in u and "?f=" in u]
    assert len(filter_calls) == 1, "must stop issuing filtered requests after detecting the ignored query"
    assert [t.tier for t in tuples] == ["B", "B"]
    assert tuples[0].theme == "tokens" and tuples[1].theme == "sacrifice"
    # Distinct conditional distributions per tuple
    p0 = tuples[0].cohort.p_smooth("Zeta")
    p1 = tuples[1].cohort.p_smooth("Zeta")
    assert p0 != pytest.approx(p1)
    assert tuples[0].cohort.p_smooth("Zeta") > d0.p_smooth("Zeta") > tuples[1].cohort.p_smooth("Zeta")


def test_real_filter_page_is_tier_a():
    slug = "korvold-fae-cursed-king"
    d0 = cohort("D0", 1000, {"Alpha": (600, 0.2), "Beta": (500, 0.2), "Zeta": (100, 0.0)})
    page = filtered_html(400, {"Alpha": (400, 0.5), "Beta": (400, 0.5), "Zeta": (120, 0.2)})
    url = filtered_page_url(slug, ["alpha", "beta"])
    session = FakeSession({url: FakeResp(200, page, "text/html")})
    ev = Evaluator(session, C.EvaluateOptions(), fetcher=make_fetcher(session), metas=CardMetaLookup(lambda n: None),
                   role_tagger=None, log=lambda m: None)
    tuples = ev._tuple_cohorts(slug, [("Alpha", "Beta")], [], d0, ["Alpha", "Beta"])
    assert tuples[0].tier == "A" and tuples[0].n_eff == 400
    assert tuples[0].cohort.p_raw("Zeta") == pytest.approx(0.3)


# =========================================================================== spec invariant 3


def test_leave_one_out_no_self_leakage():
    d0 = cohort("D0", 10000, {"Anchor A": (3000, 0.2), "Anchor B": (2500, 0.2), "Anchor C": (2000, 0.1),
                              "Anchor D": (2000, 0.1), "Trap": (3000, 0.1), "Good": (1500, 0.2)})
    # Cohort 1 is defined by A + B: A appears in 100% of it by construction.
    c1 = cohort("A+B", 800, {"Anchor A": (800, 0.8), "Anchor B": (800, 0.8), "Trap": (40, -0.2), "Good": (500, 0.4)})
    c2 = cohort("C+D", 600, {"Anchor A": (90, 0.0), "Anchor C": (600, 0.8), "Anchor D": (600, 0.8),
                             "Trap": (30, -0.2), "Good": (380, 0.4)})
    tuples = [A1.TupleCohort(("Anchor A", "Anchor B"), c1, 800, "A"), A1.TupleCohort(("Anchor C", "Anchor D"), c2, 600, "A")]

    loo = A1.bag_score("Anchor A", tuples, d0)
    assert loo.cohorts_used == 1
    assert loo.e_context == pytest.approx(c2.p_smooth("Anchor A"))  # only the cohort A did not define
    assert loo.e_context < 0.2  # no leakage of the 100% self-inclusion

    trap = A1.bag_score("Trap", tuples, d0)
    assert d0.p_raw("Trap") == pytest.approx(0.30)
    assert trap.bag_lift < 0.78 and trap.trap_penalty > 0
    good = A1.bag_score("Good", tuples, d0)
    assert good.bag_lift > 1.0

    # Full S1..S5 pipeline: the trap must score negative S1 and carry the trap tag.
    deck = []
    for name in ("Anchor A", "Anchor B", "Anchor C", "Anchor D", "Trap", "Good"):
        b = A1.bag_score(name, tuples, d0)
        cs = scores(name, True, raw=(b.raw, 0.0, -0.1 if name == "Trap" else 0.3, 0.0),
                    p_raw_d0=d0.p_raw(name), bag_lift=b.bag_lift, e_context=b.e_context)
        deck.append(cs)
    A5.normalise_scores(deck)
    A5.apply_s5(deck, C.DEFAULT_WEIGHTS)
    trap_card = next(c for c in deck if c.name == "Trap")
    assert trap_card.s[0] < 0
    assert A5.TAG_WRONG_ARCHETYPE in trap_card.tags


# =========================================================================== spec invariant 4


def test_survivor_sample_size_invariance():
    d_high = cohort("high", 400, {"Cheap Survivor": (240, 0.3), "Clunky": (0, 0.0), "Staple": (380, 0.1),
                                  "Filler": (60, 0.0), "Filler 2": (80, 0.0)})
    d_low = cohort("low", 8000, {"Cheap Survivor": (2400, 0.1), "Clunky": (2800, 0.2), "Staple": (7600, 0.1),
                                 "Filler": (1200, 0.0), "Filler 2": (1600, 0.0)})
    absent = A2.survivor_score("Nowhere Card", meta("Nowhere Card"), d_high, d_low)
    assert absent.orr == 1.0 and absent.raw == 0.0 and absent.zero_signal

    survivor = A2.survivor_score("Cheap Survivor", meta("Cheap Survivor", price=2.0), d_high, d_low)
    assert survivor.orr > 1.15 and survivor.raw > 0
    clunky = A2.survivor_score("Clunky", meta("Clunky", cmc=6), d_high, d_low)
    assert clunky.orr < 0.5 and clunky.raw < 0

    deck = []
    for name, price, cmc in (("Cheap Survivor", 2.0, 2), ("Clunky", 0.5, 6), ("Staple", 1.0, 1),
                             ("Filler", 1.0, 3), ("Filler 2", 1.0, 3), ("Nowhere Card", 1.0, 3)):
        m = meta(name, price=price, cmc=cmc)
        s = A2.survivor_score(name, m, d_high, d_low)
        deck.append(scores(name, True, m, raw=(0, s.raw, 0, 0), orr=s.orr))
    A5.normalise_scores(deck)
    A5.apply_s5(deck, C.DEFAULT_WEIGHTS)
    by = {c.name: c for c in deck}
    assert A5.TAG_BUDGET_SURVIVOR in by["Cheap Survivor"].tags
    assert by["Nowhere Card"].s[1] == pytest.approx(A5.project(0.0, *A5.reference_frame([c.raw[1] for c in deck])))
    assert A5.TAG_BUDGET_SURVIVOR not in by["Nowhere Card"].tags


def test_unlisted_card_is_censored_not_zero():
    page = cohort("p", 1000, {"A": (500, 0.1), "B": (60, 0.0)})  # listing floor = 6%
    assert page.p_raw("Missing") == 0.0
    assert page.p_est("Missing") == pytest.approx(0.03)
    assert page.p_smooth("Missing") > (C.PRIOR_STRENGTH * C.PRIOR_RATE) / (1000 + C.PRIOR_STRENGTH)


# =========================================================================== spec invariant 5


def test_secret_tech_override_protection():
    d0 = cohort("D0", 10000, {"Pet Card": (150, 0.01), "Anchor": (3000, 0.2), "Other": (2000, 0.1)})
    assert d0.p_raw("Pet Card") == pytest.approx(0.015)
    hubs = [(f"Hub {i}", cohort(f"hub{i}", 50000, {"Pet Card": (6000, None, 3.0, 40000)}, "hub")) for i in range(3)]
    h = A3.hub_score("Pet Card", hubs)
    assert h.k_hubs == 3

    deck = [
        scores("Pet Card", True, raw=(0.30, 0.0, h.raw, 0.0), p_raw_d0=0.015, bag_lift=2.0, k_hubs=3),
        scores("Anchor", True, raw=(0.10, 0.0, 0.05, 0.0), p_raw_d0=0.30),
        scores("Other", True, raw=(0.05, 0.0, 0.02, 0.0), p_raw_d0=0.20),
        scores("Filler", True, raw=(0.04, 0.0, 0.01, 0.0), p_raw_d0=0.10),
        scores("Filler 2", True, raw=(0.02, 0.0, 0.00, 0.0), p_raw_d0=0.10),
    ]
    A5.normalise_scores(deck)
    A5.apply_s5(deck, C.DEFAULT_WEIGHTS)
    pet = deck[0]
    assert pet.s[0] >= 25 and pet.s[2] >= 25
    assert A5.TAG_SECRET_TECH in pet.tags
    assert pet.s5 == A5.weighted(pet.s, C.SECRET_TECH_WEIGHTS)
    assert pet not in A5.cut_candidates(deck)


# =========================================================================== spec invariant 6


def test_asymmetric_curve_gravity_and_role_floors():
    two_drops = [meta(f"Two {i}", cmc=2) for i in range(14)]
    others = [meta(f"Three {i}", cmc=3) for i in range(8)]
    ramp = [meta(f"Ramp {i}", cmc=2, type_line="Artifact", roles={"RAMP"}) for i in range(7)]
    wipe = [meta("Only Wipe", cmc=4, type_line="Sorcery", roles={"BOARD_WIPE"})]
    deck = two_drops + others + ramp + wipe
    avg = [meta(f"Avg2 {i}", cmc=2) for i in range(8)] + [meta(f"Avg3 {i}", cmc=3) for i in range(10)] + \
          [meta(f"Avg6 {i}", cmc=6) for i in range(6)] + [meta(f"Avg4 {i}", cmc=4) for i in range(6)]
    model = A4.build_model(deck, [meta("Forest", land=True, type_line="Basic Land — Forest")] * 36, avg, None)

    raw2 = (model.user_cmc["2"] - model.target_cmc["2"]) / max(model.target_cmc["2"], 0.05)
    assert raw2 > 0
    assert model.delta_cmc["2"] == pytest.approx(raw2 * C.LOW_CMC_BLOAT_FACTOR)
    assert model.user_cmc["6+"] == 0 and model.delta_cmc["6+"] == pytest.approx(C.HIGH_CMC_STARVE_CLAMP)

    # Identical cards except mana value: cutting a 2-drop for a 6-drop must not clear the swap bar.
    cut = scores("Two 0", True, two_drops[0])
    add = scores("Six Drop", False, meta("Six Drop", cmc=6, price=1.0))
    for c in (cut, add):
        c.s = [0.0, 0.0, 0.0, round(100 * math.tanh(-model.raw_score(c.meta)), 1)]
        c.s5 = 5.0
    gain, _ = A5.swap_gain(cut, add)
    assert gain < C.MIN_SWAP_GAIN

    # Role floors: 1 wipe (< 2) and 7 ramp (< 10) are starved.
    assert model.role_boost(wipe[0]) > 0 and model.role_boost(ramp[0]) > 0
    assert model.scarce_roles_if_cut(wipe[0]) == {"BOARD_WIPE"}
    assert model.scarce_roles_if_cut(ramp[0]) == {"RAMP"}
    assert model.role_boost(two_drops[0]) == 0


# =========================================================================== spec invariant 7


def test_in_deck_reference_normalization_stability():
    deck = [scores(f"D{i}", True, raw=(0.05 * i, 0.02 * i, 0.03 * i, -0.01 * i)) for i in range(15)]
    frames_before = [A5.reference_frame([c.raw[k] for c in deck]) for k in range(4)]
    externals = [scores(f"X{i}", False, raw=(0.0, 0.0, 0.0, 0.0)) for i in range(500)]
    frames_after = A5.normalise_scores(deck + externals)
    for (m0, s0), (m1, s1) in zip(frames_before, frames_after):
        assert m0 == pytest.approx(m1) and s0 == pytest.approx(s1)
    median_card = deck[7]  # raw values are monotone, so index 7 of 15 is the median
    assert median_card.s == [0.0, 0.0, 0.0, 0.0]
    assert all(-100 <= v <= 100 for c in deck + externals for v in c.s)


# =========================================================================== spec invariant 8


def test_hungarian_matcher_role_and_positive_gain_constraints():
    def deck_card(name, s5, roles=(), protected=False, scarce=(), cmc=3, pool="A"):
        m = meta(name, cmc=cmc, roles=roles)
        c = scores(name, True, m, protected=protected, pool=pool)
        c.s5 = s5
        c.s = [s5, s5, s5, 0.0]
        c.scarce_roles = set(scarce)
        c.requires_same_role_replacement = bool(scarce)
        return c

    def ext_card(name, s5, price=2.0, roles=(), cmc=2, known=True):
        c = scores(name, False, meta(name, cmc=cmc, price=price, roles=roles, known=known))
        c.s5 = s5
        c.s = [s5, s5, s5, 0.0]
        return c

    deck = [
        deck_card("Protected Pet", -80, protected=True),
        deck_card("Only Wipe", -60, roles={"BOARD_WIPE"}, scarce={"BOARD_WIPE"}, cmc=4),
        deck_card("Weak Card", -40),
        deck_card("Fine Card", 5),
        deck_card("Staple", -10, pool="U"),
    ]
    adds = [
        ext_card("Great Add", 70),
        ext_card("Pricey Add", 90, price=200.0),
        ext_card("Unknown Price", 95, known=False),
        ext_card("Marginal Add", 16),
    ]
    cuts = A5.cut_candidates(deck)
    assert {c.name for c in cuts} == {"Only Wipe", "Weak Card", "Fine Card"}  # protected + staple excluded
    pool = A5.add_candidates(adds, budget=25.0)
    assert {c.name for c in pool} == {"Great Add", "Marginal Add"}

    swaps = A5.match_swaps(cuts, pool)
    for p in swaps:
        assert p.gain > C.MIN_SWAP_GAIN and p.delta >= C.MIN_SWAP_DELTA
        assert p.cut.name != "Protected Pet"
        if p.cut.requires_same_role_replacement:
            assert p.cut.scarce_roles <= p.add.meta.roles
    assert "Only Wipe" not in {p.cut.name for p in swaps}
    # Fine Card -> Marginal Add fails the delta bar, so it can't tie up an add: both cuts get a valid partner.
    assert {(p.cut.name, p.add.name) for p in swaps} == {("Weak Card", "Marginal Add"), ("Fine Card", "Great Add")}
    # a wipe replacement is accepted
    pool.append(ext_card("Cheap Wipe", 40, roles={"BOARD_WIPE"}, cmc=3))
    swaps = A5.match_swaps(cuts, pool)
    assert ("Only Wipe", "Cheap Wipe") in {(p.cut.name, p.add.name) for p in swaps}


# =========================================================================== parsing & slugs


def test_clean_card_line_formats():
    assert clean_card_line("1x Sol Ring (CMM) 432 *F* #Ramp")[:3] == (1, "Sol Ring", False)
    assert clean_card_line("1 Sol Ring")[:2] == (1, "Sol Ring")
    assert clean_card_line("1x Sol Ring (cmm) 432 [Ramp{top}] ^Have,#37d67a^")[1] == "Sol Ring"
    assert clean_card_line("12 Forest (FDN) 280")[:2] == (12, "Forest")
    q, n, protected, _ = clean_card_line("1 Phyrexian Altar #!protect")
    assert n == "Phyrexian Altar" and protected
    assert clean_card_line("1 Bala Ged Recovery // Bala Ged Sanctuary (ZNR) 180")[1] == "Bala Ged Recovery // Bala Ged Sanctuary"
    assert clean_card_line("1x Phyrexian Altar # !protect")[2] is True


def test_parse_decklist_sections_and_validation():
    text = "Commander\n1 Korvold, Fae-Cursed King (EOC) 120\n\nDeck\n1x Sol Ring\n1 Mayhem Devil #!protect\n30 Forest\n"
    deck = parse_decklist_text(text)
    assert deck.commanders == ["Korvold, Fae-Cursed King"]
    assert {e.name for e in deck.cards} == {"Sol Ring", "Mayhem Devil", "Forest"}
    assert deck.protected_names() == {"mayhem devil"}
    assert any("expected 100" in w for w in deck.warnings)
    mtgo = "1 Sol Ring\n1 Mayhem Devil\n\n1 Korvold, Fae-Cursed King\n"
    assert parse_decklist_text(mtgo).commanders == ["Korvold, Fae-Cursed King"]
    opt = parse_decklist_text("1 Sol Ring\n", "Tymna the Weaver; Kraum, Ludevic's Opus")
    assert opt.commanders == ["Tymna the Weaver", "Kraum, Ludevic's Opus"]
    assert parse_decklist_text("COMMANDER: Korvold, Fae-Cursed King\n1 Sol Ring").commanders == ["Korvold, Fae-Cursed King"]
    full = "Commander\n1 Korvold, Fae-Cursed King\nDeck\n" + "".join(f"1 Card {i}\n" for i in range(60)) + "39 Forest\n"
    assert not [w for w in parse_decklist_text(full).warnings if "expected 100" in w]


def test_slugs():
    assert edhrec_slug("Korvold, Fae-Cursed King") == "korvold-fae-cursed-king"
    assert commander_slug(["Tymna the Weaver", "Kraum, Ludevic's Opus"]) == "kraum-ludevics-opus-tymna-the-weaver"
    assert commander_slug(["Kraum, Ludevic's Opus", "Tymna the Weaver"]) == "kraum-ludevics-opus-tymna-the-weaver"
    assert edhrec_slug("Séance Board") == "seance-board"
    assert edhrec_slug("Andúril, Flame of the West") == "anduril-flame-of-the-west"
    assert edhrec_slug("Wear // Tear") == "wear-tear"
    assert edhrec_slug("V.A.T.S.") == "vats"
    assert commander_slug(["Bala Ged Recovery // Bala Ged Sanctuary"]) == "bala-ged-recovery"


def test_parse_real_edhrec_pages():
    c = parse_filtered_html((FIXTURES / "edhrec_filtered_korvold.html").read_text(), "fx", commander="korvold-fae-cursed-king")
    assert c.num_decks == 3920
    assert c.p_raw("Mayhem Devil") == pytest.approx(3424 / 3920)
    assert c.p_raw("Crop Rotation") == 1.0  # a filter card is in every deck of its own cohort
    d0 = parse_json_page(json.loads((FIXTURES / "edhrec_commander_korvold.json").read_text()), "D0")
    assert d0.num_decks == 21053
    stat = d0.get("Crop Rotation") or next(iter(d0.cards.values()))
    assert stat.potential_decks > 0 and stat.synergy is not None
    assert d0.raw["panels"]["mana_curve"]
    assert A4.types_from_page(d0.raw)["Creature"] > 0.3


def test_hub_pages_use_published_lift():
    hub = parse_json_page({"container": {"json_dict": {"card": {"num_decks": 1000}, "cardlists": [
        {"tag": "topcommanders", "cardviews": [{"name": "Some Commander", "num_decks": 900, "potential_decks": 1000}]},
        {"tag": "creatures", "cardviews": [{"name": "Blood Artist", "lift": 4.2, "num_decks": 480, "potential_decks": 1000}]},
    ]}}}, "hub", source="hub")
    assert not hub.present("Some Commander")  # commander lists are not card co-occurrence
    assert hub.lift("Blood Artist") == pytest.approx(4.2)


# =========================================================================== fetcher safety


def test_fetcher_treats_s3_403_as_missing_and_challenge_as_block():
    missing = f"{C.EDHREC_JSON_BASE}/commanders/x/cheap.json"
    blocked = f"{C.EDHREC_JSON_BASE}/commanders/x.json"
    after = f"{C.EDHREC_JSON_BASE}/commanders/y.json"
    session = FakeSession({
        missing: FakeResp(403, "<?xml version='1.0'?><Error><Code>AccessDenied</Code></Error>", "application/xml"),
        blocked: FakeResp(200, "<html><title>Just a moment...</title>cloudflare</html>", "text/html"),
        after: FakeResp(200, "{}"),
    })
    f = make_fetcher(session)
    assert f.fetch(missing) is None
    assert f.telemetry.blocked is False and missing in f.telemetry.missing
    assert f.fetch(missing) is None and session.network_calls.count(missing) == 1  # remembered
    assert f.fetch(blocked) is None and f.telemetry.blocked is True
    assert f.fetch(after) is None and after not in session.network_calls  # breaker open: no more traffic


def test_fetcher_budget_cache_redirect_and_backoff():
    base = C.EDHREC_JSON_BASE
    cached_url = f"{base}/commanders/cached.json"
    stale_url = f"{base}/commanders/stale.json"
    rev = f"{base}/commanders/tymna-the-weaver-kraum-ludevics-opus.json"
    real = f"{base}/commanders/kraum-ludevics-opus-tymna-the-weaver.json"
    slow = f"{base}/commanders/slow.json"
    sleeps = []
    session = FakeSession(
        routes={
            rev: FakeResp(200, json.dumps({"redirect": "/commanders/kraum-ludevics-opus-tymna-the-weaver"})),
            real: FakeResp(200, json.dumps({"ok": 1})),
            slow: [FakeResp(429, "", headers={"Retry-After": "7"}), FakeResp(200, json.dumps({"ok": 2}))],
            stale_url: FakeResp(200, json.dumps({"fresh": True})),
        },
        cache={
            cached_url: FakeResp(200, json.dumps({"cached": True}), from_cache=True),
            stale_url: FakeResp(200, json.dumps({"fresh": False}), from_cache=True, age_days=30),
        },
    )
    f = EdhrecFetcher(session, max_requests=5, ttl_days=7, sleep=sleeps.append, rand=lambda a, b: a,
                      missing_cache_path=None)
    assert f.fetch(cached_url) == {"cached": True}
    assert f.budget.used == 0 and f.telemetry.edhrec_cache_hits == 1
    assert f.fetch(stale_url) == {"fresh": True}  # older than the TTL -> refetched
    assert f.fetch(rev) == {"ok": 1}  # redirect followed
    assert f.fetch(slow) == {"ok": 2}
    assert 7.0 in sleeps  # Retry-After honoured
    assert f.budget.used == 5
    assert f.fetch(f"{base}/commanders/over-budget.json") is None
    assert f.telemetry.budget_exhausted


def test_rate_limiter_spaces_requests():
    sleeps = []
    t = [100.0]
    from edhparse.evaluate.edhrec import JitterRateLimiter

    lim = JitterRateLimiter((1.8, 3.2), sleep=lambda s: (sleeps.append(s), t.__setitem__(0, t[0] + s)),
                            rand=lambda a, b: 2.5, clock=lambda: t[0])
    lim.wait()
    lim.wait()
    assert sleeps == [pytest.approx(2.5)]


# =========================================================================== Scryfall metadata & roles


def test_meta_normalises_mdfc_split_and_transform():
    mdfc = meta_from_scryfall({"name": "Bala Ged Recovery // Bala Ged Sanctuary", "layout": "modal_dfc", "cmc": 3,
                               "color_identity": ["G"], "type_line": "Sorcery // Land", "prices": {"usd": None, "usd_foil": "8.11"},
                               "legalities": {"commander": "legal"},
                               "card_faces": [{"type_line": "Sorcery", "oracle_text": "Return target card", "mana_cost": "{2}{G}"},
                                              {"type_line": "Land", "oracle_text": "{T}: Add {G}."}]})
    assert not mdfc.is_land and mdfc.is_mdfc_land and mdfc.primary_type == "Sorcery"
    assert mdfc.price == pytest.approx(8.11) and mdfc.price_known
    assert "Add {G}" in mdfc.oracle_text
    split = meta_from_scryfall({"name": "Dusk // Dawn", "layout": "split", "cmc": 9, "type_line": "Sorcery // Sorcery",
                                "card_faces": [{"mana_cost": "{2}{W}{W}"}, {"mana_cost": "{3}{W}{W}"}], "prices": {}})
    assert split.cmc == 4 and not split.price_known
    land = meta_from_scryfall({"name": "Forest", "type_line": "Basic Land — Forest", "cmc": 0, "prices": {}})
    assert land.is_land and land.is_basic
    banned = meta_from_scryfall({"name": "X", "type_line": "Artifact", "legalities": {"commander": "banned"}})
    assert not banned.legal


def test_role_tagger_queries_in_chunks_and_caches():
    names = [f"Card Number {i}" for i in range(80)]
    chunks = chunk_names(names)
    assert len(chunks) > 1 and all(len(build_role_query("REMOVAL", ch)) < 1000 for ch in chunks)

    hits = {"RAMP": {"Card Number 1", "Big Ramp"}, "DRAW": {"Card Number 2"}, "REMOVAL": {"Card Number 3"},
            "BOARD_WIPE": set()}

    def route(url):
        from urllib.parse import parse_qs, urlparse

        q = parse_qs(urlparse(url).query)["q"][0]
        role = next(r for r, pre in C.ROLE_TAG_QUERIES.items() if q.startswith(pre + " ("))
        found = [n for n in hits[role] if f'"{n}"' in q]
        if not found:
            return FakeResp(404, json.dumps({"object": "error"}))
        return FakeResp(200, json.dumps({"data": [{"name": n} for n in found], "has_more": False}))

    class RouteSession(FakeSession):
        def get(self, url, params=None, **kw):
            from urllib.parse import urlencode

            full = url + ("?" + urlencode(params) if params else "")
            self.network_calls.append(full)
            return route(full)

    session = RouteSession()
    from edhparse.evaluate.edhrec import Telemetry

    tel = Telemetry()
    cache = RoleTagCache(":memory:")
    tagger = RoleTagger(session, tel, cache=cache, sleep=no_sleep)
    metas = [meta(n, cmc=2) for n in names[:5]] + [meta("Big Ramp", cmc=6)]
    tagger.tag(metas)
    by = {m.name: m.roles for m in metas}
    assert by["Card Number 1"] == {"RAMP"} and by["Card Number 2"] == {"DRAW"} and by["Card Number 3"] == {"REMOVAL"}
    assert by["Big Ramp"] == set()  # ramp needs MV <= 4
    first = len(session.network_calls)
    assert first == 4
    tagger2 = RoleTagger(session, tel, cache=cache, sleep=no_sleep)
    tagger2.tag([meta(n, cmc=2) for n in names[:5]])
    assert len(session.network_calls) == first  # answered from the per-card cache


def test_role_tagger_falls_back_to_regex_on_failure():
    class Broken(FakeSession):
        def get(self, *a, **k):
            return FakeResp(500, "boom")

    from edhparse.evaluate.edhrec import Telemetry

    tel = Telemetry()
    m = meta("Wrath", cmc=4, type_line="Sorcery")
    m.oracle_text = "Destroy all creatures. They can't be regenerated."
    RoleTagger(Broken(), tel, cache=RoleTagCache(":memory:"), sleep=no_sleep).tag([m])
    assert m.roles == {"BOARD_WIPE"}
    assert any("fell back" in line for line in tel.log)


# =========================================================================== end to end (offline)


def _world():
    """A small synthetic commander world served through a fake session."""
    slug = "test-commander"
    base = C.EDHREC_JSON_BASE
    names = {f"Anchor {i}": ("Creature — Elf" if i % 2 else "Enchantment", 2 + i % 3) for i in range(10)}
    names.update({"Staple Ring": ("Artifact", 1), "Pet Card": ("Creature — Elf", 3), "Clunky Six": ("Creature — Giant", 6),
                  "Budget Gem": ("Instant", 2), "Pricey Gem": ("Artifact", 0), "Ramp Spell": ("Sorcery", 2),
                  "Wipe Spell": ("Sorcery", 4), "Utility Land": ("Land", 0), "Better Land": ("Land", 0)})
    prices = {"Pricey Gem": 300.0, "Budget Gem": 2.0}
    roles = {"Ramp Spell": {"RAMP"}, "Wipe Spell": {"BOARD_WIPE"}, "Staple Ring": {"RAMP"}}

    def card(n):
        tl, cmc = names[n] if n in names else ("Basic Land — Forest", 0)
        return {"name": n, "layout": "normal", "cmc": cmc, "type_line": tl, "color_identity": ["G"],
                "prices": {"usd": str(prices.get(n, 1.0))}, "legalities": {"commander": "legal"}, "oracle_text": ""}

    def page(n, cards, lists=None):
        return json.dumps({"creature": 30, "instant": 10, "sorcery": 10, "artifact": 10, "enchantment": 5, "planeswalker": 0,
                           "panels": {"mana_curve": {"1": 10, "2": 20, "3": 15, "4": 8, "5": 4, "6": 3},
                                      "taglinks": [{"slug": "elves", "count": 300}]},
                           "container": {"json_dict": {"card": {"num_decks": n}, "cardlists": lists or [{"tag": "cards", "cardviews": [
                               {"name": k, "sanitized": edhrec_slug(k), "num_decks": v[0], "potential_decks": n, "synergy": v[1]}
                               for k, v in cards.items()]}]}}})

    d0_cards = {f"Anchor {i}": (300 - 20 * i, 0.15) for i in range(10)}
    d0_cards.update({"Staple Ring": (900, 0.05), "Pet Card": (40, 0.0), "Clunky Six": (250, 0.05), "Budget Gem": (200, 0.1),
                     "Pricey Gem": (150, 0.1), "Ramp Spell": (300, 0.0), "Wipe Spell": (200, 0.0),
                     "Utility Land": (100, 0.0), "Better Land": (500, 0.1)})
    routes = {
        f"{base}/commanders/{slug}.json": FakeResp(200, page(1000, d0_cards)),
        f"{base}/commanders/{slug}/expensive.json": FakeResp(200, page(100, {**{k: (int(v[0] / 10), v[1]) for k, v in d0_cards.items()},
                                                                         "Budget Gem": (60, 0.3), "Clunky Six": (1, 0.0)})),
        f"{base}/commanders/{slug}/budget.json": FakeResp(200, page(100, {**{k: (int(v[0] / 10), v[1]) for k, v in d0_cards.items()},
                                                                      "Budget Gem": (10, 0.0), "Clunky Six": (60, 0.2)})),
        f"{base}/average-decks/{slug}/expensive.json": FakeResp(200, json.dumps({"deck": {"cards": {"x": [[f"Anchor {i}", 1] for i in range(10)]}}})),
    }

    def filt(url):
        return FakeResp(200, filtered_html(200, {**{f"Anchor {i}": (150, 0.3) for i in range(10)},
                                                 "Budget Gem": (120, 0.4), "Clunky Six": (5, -0.2), "Staple Ring": (190, 0.05)},
                                           commander=slug), "text/html")

    class WorldSession(FakeSession):
        def get(self, url, params=None, only_if_cached=False, **kw):
            if only_if_cached:
                return FakeResp(504)
            self.network_calls.append(url)
            if "edhrec.com/commanders/" in url and "?f=" in url:
                return filt(url)
            if "/pages/cards/" in url:
                return FakeResp(200, json.dumps({"container": {"json_dict": {"card": {"num_decks": 5000}, "cardlists": [
                    {"tag": "creatures", "cardviews": [{"name": "Budget Gem", "num_decks": 1500, "potential_decks": 5000, "lift": 3.0},
                                                       {"name": "Pet Card", "num_decks": 900, "potential_decks": 5000, "lift": 2.0}]}]}}}))
            return routes.get(url) or FakeResp(403, "<Error><Code>AccessDenied</Code></Error>", "application/xml")

    class Tagger:
        failed = False

        def tag(self, metas):
            for m in metas:
                m.roles = set(roles.get(m.name, ()))

    deck_text = "Commander\n1 Test Commander\n\nDeck\n" + "".join(f"1 Anchor {i}\n" for i in range(10)) + \
        "1 Staple Ring\n1 Pet Card #!protect\n1 Clunky Six\n1 Ramp Spell\n1 Wipe Spell\n1 Utility Land\n30 Forest\n"
    lookup = {k.lower(): card(k) for k in list(names) + ["Forest"]}
    lookup["test commander"] = {"name": "Test Commander", "layout": "normal", "cmc": 4, "type_line": "Legendary Creature — Elf",
                                "color_identity": ["G"], "prices": {"usd": "1"}, "legalities": {"commander": "legal"}}
    return WorldSession(), Tagger(), deck_text, (lambda n: lookup.get(n.lower()))


def test_end_to_end_offline(tmp_path):
    session, tagger, deck_text, lookup = _world()
    fetcher = make_fetcher(session)
    ev = Evaluator(session, C.EvaluateOptions(tuples=4, hubs=3), fetcher=fetcher, metas=CardMetaLookup(lookup),
                   role_tagger=tagger, log=lambda m: None)
    res = ev.run(parse_decklist_text(deck_text))
    assert res.slug == "test-commander"
    assert res.telemetry.filter_mode.startswith("Tier A")
    assert fetcher.budget.used <= C.DEFAULT_MAX_REQUESTS
    assert all(t.tier == "A" for t in res.tuples)
    names_in_deck = {c.name for c in res.deck_scores}
    assert "Utility Land" not in names_in_deck and "Forest" not in names_in_deck
    adds = {c.name for c in res.adds}
    assert "Budget Gem" in adds and "Pricey Gem" not in adds
    assert "Pet Card" not in {c.name for c in res.cuts}  # protected
    clunky = next(c for c in res.deck_scores if c.name == "Clunky Six")
    assert clunky.s5 < 0
    assert any(s.add.name == "Budget Gem" for s in res.swaps)
    assert [u.add for u in res.land_upgrades] == ["Better Land"]

    from renderers.evaluate_renderer import write_reports

    out = write_reports(res, str(tmp_path / "r.html"), str(tmp_path / "r.md"), str(tmp_path / "r.csv"))
    assert len(out) == 3
    html_text = (tmp_path / "r.html").read_text()
    for section in ("Telemetry", "Structure", "cuts", "adds", "swaps", "land"):
        assert section.lower() in html_text.lower()
    rows = (tmp_path / "r.csv").read_text().splitlines()
    assert rows[0].startswith("name,in_deck") and len(rows) > 15

    # Warm re-run: everything now comes from the memo/cache, zero new network calls.
    calls = len(session.network_calls)
    ev.run(parse_decklist_text(deck_text))
    assert len(session.network_calls) == calls


def test_web_evaluate_form_and_route():
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from edhparse.web.app import app

    client = TestClient(app)
    r = client.get("/workflow/evaluate")
    assert r.status_code == 200 and "<form" in r.text and "deck_text" in r.text
    with patch("edhparse.services.evaluate.run_evaluate") as run:
        run.return_value = {"ok": True, "message": "Evaluated X", "outputs": []}
        r = client.post("/run/evaluate", data={"deck_text": "1 Sol Ring", "commander": "X"})
        assert r.status_code == 200 and "Evaluated X" in r.text
        assert run.call_args.kwargs["deck_text"] == "1 Sol Ring"


# =========================================================================== phase 1: resilience & primitives


def _challenge():
    return FakeResp(200, "<title>Just a moment...</title> cloudflare", "text/html")


def _wrap_world_session(session, filtered_response):
    """Make the end-to-end world answer every website filter page with ``filtered_response()``."""
    inner = session.get

    def get(url, params=None, only_if_cached=False, **kw):
        if not only_if_cached and "edhrec.com/commanders/" in url and "?f=" in url:
            session.network_calls.append(url)
            return filtered_response()
        return inner(url, params=params, only_if_cached=only_if_cached, **kw)

    session.get = get
    return session


def test_challenge_on_website_does_not_block_cdn():
    web = filtered_page_url("x", ["a", "b"])
    cdn = f"{C.EDHREC_JSON_BASE}/commanders/x.json"
    payload = json.dumps({"container": {"json_dict": {"card": {"num_decks": 5}, "cardlists": []}}})
    f = make_fetcher(FakeSession({web: _challenge(), cdn: FakeResp(200, payload)}))
    assert f.fetch(web, "html") is None
    assert f.telemetry.blocked is True
    assert f.is_blocked(web) and not f.is_blocked(cdn)
    assert f.fetch(cdn) is not None


def test_website_challenge_keeps_hubs_alive_end_to_end():
    session, tagger, deck_text, lookup = _world()
    _wrap_world_session(session, _challenge)
    ev = Evaluator(session, C.EvaluateOptions(tuples=4, hubs=3), fetcher=make_fetcher(session),
                   metas=CardMetaLookup(lookup), role_tagger=tagger, log=lambda m: None)
    res = ev.run(parse_decklist_text(deck_text))
    assert res.hubs, "S3 hub pages live on the CDN and must survive a website block"
    assert not any("No engine hub pages" in w for w in res.warnings)


def test_top_cards_independent_of_insertion_order():
    a = cohort("a", 100, {"X": (50,), "Y": (80,), "Z": (80,)})
    b = cohort("b", 100, {"Z": (80,), "X": (50,), "Y": (80,)})
    assert a.top_cards(3) == b.top_cards(3) == [("Y", 80), ("Z", 80), ("X", 50)]


def test_unparseable_filter_html_is_a_visible_warning():
    session, tagger, deck_text, lookup = _world()
    _wrap_world_session(session, lambda: FakeResp(200, "<html>layout changed</html>", "text/html"))
    ev = Evaluator(session, C.EvaluateOptions(tuples=4, hubs=3), fetcher=make_fetcher(session),
                   metas=CardMetaLookup(lookup), role_tagger=tagger, log=lambda m: None)
    res = ev.run(parse_decklist_text(deck_text))
    assert any("filter" in w.lower() and "recogni" in w.lower() for w in res.warnings)


def test_tuple_fetching_reserves_budget_for_hubs():
    session, tagger, deck_text, lookup = _world()
    fetcher = make_fetcher(session, max_requests=8)  # 4 base pages leave 4; without a reserve tuples eat all of it
    ev = Evaluator(session, C.EvaluateOptions(tuples=4, hubs=3, max_requests=8), fetcher=fetcher,
                   metas=CardMetaLookup(lookup), role_tagger=tagger, log=lambda m: None)
    res = ev.run(parse_decklist_text(deck_text))
    assert len(res.hubs) == 3
    assert len(res.tuples) >= 1
    assert fetcher.budget.used <= 8


def test_lift_from_synergy_is_capped():
    assert lift_from_synergy(0.90, 0.89) == pytest.approx(C.MAX_LIFT)
    assert lift_from_synergy(0.50, 0.30) < C.MAX_LIFT  # ordinary values are untouched


def test_merge_cohorts_is_censor_aware_and_merges_panels():
    from edhparse.evaluate.edhrec import merge_cohorts

    a = cohort("A", 1000, {"X": (400,), "Y": (100,)})
    b = cohort("B", 500, {"Y": (100,), "Z": (25,)})
    a.raw = {"creature": 10, "panels": {"taglinks": [{"slug": "a-theme", "count": 10}], "mana_curve": {"2": 10}},
             "container": {"json_dict": {"cardlists": ["partner A only"]}}}
    b.raw = {"creature": 30, "panels": {"taglinks": [{"slug": "b-theme", "count": 20}, {"slug": "a-theme", "count": 5}],
                                        "mana_curve": {"2": 30, "3": 10}}}
    merged = merge_cohorts("M", [a, b])
    wa, wb = math.log(1000), math.log(500)
    # X is unlisted (censored below the 5% floor) on B, not 0%.
    expected_x = (wa * a.p_est("X") + wb * b.p_est("X")) / (wa + wb)
    assert merged.p_raw("X") == pytest.approx(expected_x, abs=2e-3)
    assert b.p_est("X") > 0
    panels = merged.raw["panels"]
    # theme-link counts are deck counts, so they add up ...
    assert {t["slug"]: t["count"] for t in panels["taglinks"]} == {"a-theme": 15, "b-theme": 20}
    # ... while type counts and the mana curve are per-deck averages: weighted like the card rates, not summed.
    assert panels["mana_curve"]["2"] == pytest.approx((wa * 10 + wb * 30) / (wa + wb))
    assert panels["mana_curve"]["3"] == pytest.approx((wa * 0 + wb * 10) / (wa + wb))
    assert merged.raw["creature"] == pytest.approx((wa * 10 + wb * 30) / (wa + wb))
    assert "container" not in merged.raw, "one partner's card lists must not pose as the merged page"


# =========================================================================== phase 2: signals


def test_cohort_weight_is_logarithmic_in_sample_size():
    """sqrt(N) weighting was tried and reverted: on the recorded real corpus it was worse than ln(N) on
    Korvold (-0.10 NDCG@20, -0.11 tail recall) and neutral elsewhere (python -m tests.real_backtest --ablation)."""
    def tc(n):
        return A1.TupleCohort(anchors=("a", "b"), cohort=None, n_eff=n, tier="A")

    assert tc(12).weight == pytest.approx(math.log(12))
    assert tc(2000).weight == pytest.approx(math.log(2000))
    assert tc(50).weight < tc(200).weight < tc(900).weight
    assert tc(1).weight == pytest.approx(tc(10).weight)  # tiny cohorts are floored at ln(10)
    assert A1.TupleCohort(anchors=("a", "b"), cohort=None, n_eff=100, tier="B", affinity=2.0).weight == \
        pytest.approx(2.0 * tc(100).weight)


def test_unlisted_card_on_high_floor_cohort_is_not_inflated():
    d0 = cohort("D0", 2000, {"X": (160,), "Floor": (100,), "Common": (900,)})  # floor 5%, X at 8%
    small = cohort("small", 100, {"Q": (30,), "Common": (60,)})  # tiny page: floor 30%, X unlisted
    assert small.floor_rate > d0.floor_rate and not small.present("X")
    tc = A1.TupleCohort(anchors=("a", "b"), cohort=small, n_eff=100, tier="A")
    # Unlisted on the small page only means "< 30%"; that is no evidence X is *boosted* in this build path.
    assert A1.bag_score("X", [tc], d0).bag_lift == pytest.approx(1.0, abs=0.05)
    # A genuinely collapsed card (listed far below its baseline) must still be penalised.
    small.add(CardStat(name="Y", slug="y", num_decks=31, potential_decks=100))
    d0.add(CardStat(name="Y", slug="y", num_decks=900, potential_decks=2000))
    assert A1.bag_score("Y", [tc], d0).bag_lift < C.TRAP_BAGLIFT


def _floors_model(avg_roles):
    deck = [meta(f"Filler {i}", cmc=3) for i in range(30)]
    avg = [meta(f"Avg {i}", cmc=3, roles=r) for i, r in enumerate(avg_roles)]
    return A4.build_model(deck, [], avg, None)


def test_role_floors_follow_the_tagged_average_deck_within_bounds():
    avg_roles = [{"RAMP"}] * 6 + [set()] * 30
    m = _floors_model(avg_roles)
    # floor = round(0.70 * avg count + 0.30 * default), then clamped to ROLE_FLOOR_BOUNDS
    assert m.role_floors["RAMP"] == 7  # 0.7*6 + 0.3*10 = 7.2
    assert m.role_floors["DRAW"] == C.ROLE_FLOOR_BOUNDS["DRAW"][0]  # 3.0 -> clamped up to the lower bound
    assert m.role_floors["BOARD_WIPE"] == 1  # 0.6 -> 1
    assert _floors_model([{"RAMP"}] * 40).role_floors["RAMP"] == C.ROLE_FLOOR_BOUNDS["RAMP"][1]  # clamped down
    # the floor drives deficits and the scarce-role guardrail
    wipe = meta("Wipe", cmc=4, type_line="Sorcery", roles={"BOARD_WIPE"})
    m2 = A4.build_model([wipe] + [meta(f"F{i}") for i in range(20)], [], [meta("A", roles={"RAMP"})], None)
    assert m2.deficit("BOARD_WIPE") == 0.0  # one wipe satisfies a floor of 1
    assert m2.scarce_roles_if_cut(wipe) == {"BOARD_WIPE"}


def test_untagged_average_deck_falls_back_to_default_floors():
    m = _floors_model([set()] * 36)
    assert m.role_floors == C.ROLE_FLOORS


def test_engine_role_tags_the_average_deck():
    session, tagger, deck_text, base_lookup = _world()
    avg_only = {"name": "Avg Only", "layout": "normal", "cmc": 2, "type_line": "Artifact", "color_identity": ["G"],
                "prices": {"usd": "1"}, "legalities": {"commander": "legal"}, "oracle_text": ""}
    lookup = lambda n: avg_only if n.lower() == "avg only" else base_lookup(n)  # noqa: E731
    inner = session.get

    def get(url, params=None, only_if_cached=False, **kw):
        if not only_if_cached and "/average-decks/" in url:
            session.network_calls.append(url)
            return FakeResp(200, json.dumps({"deck": {"cards": {"x": [["Avg Only", 1]]}}}))
        return inner(url, params=params, only_if_cached=only_if_cached, **kw)

    session.get = get
    seen = []
    tag = tagger.tag
    tagger.tag = lambda metas: (seen.extend(m.name for m in metas), tag(metas))[1]
    ev = Evaluator(session, C.EvaluateOptions(tuples=4, hubs=3), fetcher=make_fetcher(session),
                   metas=CardMetaLookup(lookup), role_tagger=tagger, log=lambda m: None)
    res = ev.run(parse_decklist_text(deck_text))
    assert "Avg Only" in seen, "average-deck cards must be role-tagged so their role counts can set the floors"
    assert set(res.structure.role_floors) == set(C.ROLES)


# =========================================================================== phase 3: normalisation & swaps


def test_min_signal_scales_prevent_zero_spread_blowup():
    # S2/S3/S4 are identical across the deck (e.g. no price tiers, no hubs): the spread is zero.
    deck = [scores(f"D{i}", True, raw=(0.05 * i, 0.0, 0.0, 0.0)) for i in range(15)]
    small = scores("Small", False, raw=(0.0, 0.005, 0.005, 0.01))
    big = scores("Big", False, raw=(0.0, 0.5, 0.5, 0.5))
    A5.normalise_scores(deck + [small, big])
    assert all(abs(v) < 40 for v in small.s[1:]), "a trivial raw difference must not saturate to +/-100"
    assert all(v > 90 for v in big.s[1:]), "a genuinely large effect still saturates"
    assert deck[0].s[1] == 0.0


def test_matcher_ignores_pairs_that_fail_the_delta_bar(monkeypatch):
    c1, c2 = scores("c1", True), scores("c2", True)
    a1, a2 = scores("a1", False), scores("a2", False)
    c1.s5 = c2.s5 = -20.0
    a1.s5, a2.s5 = -10.0, 30.0  # a1 improves S5 by only 10 (< MIN_SWAP_DELTA) however good its bonuses
    table = {("c1", "a1"): 60.0, ("c1", "a2"): 40.0, ("c2", "a1"): 18.0, ("c2", "a2"): 35.0}
    monkeypatch.setattr(A5, "swap_gain", lambda cut, add: (table[(cut.name, add.name)], "t"))
    swaps = A5.match_swaps([c1, c2], [a1, a2])
    # Pairing c1 with the disqualified a1 must not cost c1 its best valid partner.
    assert [(p.cut.name, p.add.name, p.gain) for p in swaps] == [("c1", "a2", 40.0)]


def test_structural_bonuses_cannot_outweigh_card_quality():
    assert C.SWAP_CURVE_BONUS + C.SWAP_TYPE_BONUS + C.SWAP_ROLE_BONUS <= 20
    cut = scores("cut", True, meta("cut", cmc=2, type_line="Artifact", roles={"RAMP"}))
    same_slot = scores("same", False, meta("same", cmc=2, type_line="Artifact", roles={"RAMP"}))
    better = scores("better", False, meta("better", cmc=3, type_line="Creature — Elf"))
    cut.s5, same_slot.s5, better.s5 = -40.0, -25.0, 5.0  # deltas: 15 vs 45
    swaps = A5.match_swaps([cut], [same_slot, better])
    assert swaps and swaps[0].add.name == "better", "a much better card beats a merely same-shaped one"


# =========================================================================== phase 4: rate limiting on the website

SOFT_429 = "<html><head><title>429 Too Many Requests | EDHREC</title></head><body>slow down</body></html>"


def _soft_429():
    return FakeResp(200, SOFT_429, "text/html")


def test_soft_429_page_is_retried_with_backoff():
    web = filtered_page_url("x", ["a", "b"])
    good = filtered_html(100, {"A": (50, 0.1)}, commander="x")
    s = FakeSession({web: [_soft_429(), FakeResp(200, good, "text/html")]})
    sleeps = []
    f = EdhrecFetcher(s, sleep=sleeps.append, rand=lambda a, b: 0.0, missing_cache_path=None)
    assert f.fetch(web, "html") == good
    assert C.BACKOFF_SCHEDULE[0] in sleeps
    assert f.budget.used == 2 and not f.telemetry.blocked


def test_persistent_soft_429_opens_the_breaker_for_that_host_only():
    web = filtered_page_url("x", ["a", "b"])
    cdn = f"{C.EDHREC_JSON_BASE}/commanders/x.json"
    payload = json.dumps({"container": {"json_dict": {"card": {"num_decks": 5}, "cardlists": []}}})
    s = FakeSession({web: lambda url: _soft_429(), cdn: FakeResp(200, payload)})
    f = EdhrecFetcher(s, sleep=no_sleep, rand=lambda a, b: 0.0, missing_cache_path=None)
    assert f.fetch(web, "html") is None
    assert f.telemetry.blocked_hosts == {"edhrec.com"}
    assert any("rate" in n.lower() for n in f.telemetry.log)
    assert f.fetch(cdn) is not None


def test_cached_soft_429_page_is_not_trusted():
    web = filtered_page_url("x", ["a", "b"])
    good = filtered_html(100, {"A": (50, 0.1)}, commander="x")
    poisoned = FakeResp(200, SOFT_429, "text/html", from_cache=True)
    s = FakeSession({web: FakeResp(200, good, "text/html")}, cache={web: poisoned})
    f = make_fetcher(s)
    assert f.fetch(web, "html") == good
    assert s.network_calls == [web], "a poisoned cache entry must be re-fetched, not served"


def test_website_pages_are_paced_slower_than_the_cdn():
    from edhparse.evaluate.edhrec import commander_url

    good = filtered_html(100, {"A": (50, 0.1)}, commander="x")
    payload = json.dumps({"container": {"json_dict": {"card": {"num_decks": 5}, "cardlists": []}}})
    w1, w2 = filtered_page_url("x", ["a", "b"]), filtered_page_url("x", ["a", "c"])
    c1, c2 = commander_url("x"), commander_url("y")
    s = FakeSession({w1: FakeResp(200, good, "text/html"), w2: FakeResp(200, good, "text/html"),
                     c1: FakeResp(200, payload), c2: FakeResp(200, payload)})
    sleeps = []
    f = EdhrecFetcher(s, sleep=sleeps.append, rand=lambda lo, hi: hi, missing_cache_path=None)
    f.fetch(w1, "html")
    f.fetch(w2, "html")
    web_wait = max(sleeps)
    sleeps.clear()
    f.fetch(c1)
    f.fetch(c2)
    cdn_wait = max(sleeps)
    assert C.EDHREC_DELAY_RANGE[1] < web_wait <= C.EDHREC_WEB_DELAY_RANGE[1]
    assert cdn_wait <= C.EDHREC_DELAY_RANGE[1]


def test_empty_filter_cohort_is_not_mistaken_for_a_layout_change():
    session, tagger, deck_text, lookup = _world()
    _wrap_world_session(session, lambda: FakeResp(200, filtered_html(0, {}), "text/html"))  # "no decks run both"
    ev = Evaluator(session, C.EvaluateOptions(tuples=4, hubs=3), fetcher=make_fetcher(session),
                   metas=CardMetaLookup(lookup), role_tagger=tagger, log=lambda m: None)
    res = ev.run(parse_decklist_text(deck_text))
    assert res.telemetry.filter_parse_failed is False
    assert not any("recogni" in w.lower() for w in res.warnings)
    assert any("no decks run both" in n for n in res.telemetry.log)


def test_cache_layer_refuses_to_store_a_soft_429_page():
    from clients.session import _should_cache_response

    class R:
        status_code = 200

        def __init__(self, text):
            self.text = text

    assert _should_cache_response(R(SOFT_429)) is False
    assert _should_cache_response(R("<html><head><title>Korvold | EDHREC</title></head></html>")) is True


# =========================================================================== phase 3: evaluator fixes


def test_prescreen_spends_finalist_slots_only_on_affordable_cards(monkeypatch):
    monkeypatch.setattr(C, "FINALISTS", 2)
    deck = [scores(f"D{i}", True, raw=(0.05 * i, 0.01 * i, 0.01 * i, 0.0)) for i in range(10)]
    pricey = [scores(f"P{i}", False, meta(f"P{i}", price=300.0), raw=(5.0, 5.0, 5.0, 0.0)) for i in range(3)]
    unpriced = scores("U", False, meta("U", known=False), raw=(4.0, 4.0, 4.0, 0.0))
    cheap = [scores(f"C{i}", False, meta(f"C{i}", price=2.0), raw=(1.0 + i, 1.0, 1.0, 0.0)) for i in range(3)]
    finalists = Evaluator._prescreen(deck, pricey + [unpriced] + cheap, (0.4, 0.3, 0.3, 0.0), C.DEFAULT_MAX_ADD_BUDGET)
    assert {c.name for c in finalists} == {"C2", "C1"}  # unbuyable cards no longer crowd out affordable ones
    assert all(c.finalist for c in finalists) and not any(c.finalist for c in pricey + [unpriced])


def test_stale_cache_is_served_when_the_refresh_is_rate_limited():
    web = filtered_page_url("x", ["a", "b"])
    good = filtered_html(100, {"A": (50, 0.1)}, commander="x")
    stale = FakeResp(200, good, "text/html", from_cache=True, age_days=30)
    s = FakeSession({web: lambda url: _soft_429()}, cache={web: stale})
    f = EdhrecFetcher(s, sleep=no_sleep, rand=lambda a, b: 0.0, missing_cache_path=None)
    assert f.fetch(web, "html") == good, "an old copy beats no data when the refresh fails"
    assert f.telemetry.edhrec_cache_hits == 1


def test_filter_urls_do_not_depend_on_argument_order():
    a = filtered_page_url("cmd", ["b-card", "a-card"], ["y-card", "x-card"])
    b = filtered_page_url("cmd", ["a-card", "b-card"], ["x-card", "y-card"])
    assert a == b


def test_per_run_telemetry_flags_do_not_leak_between_runs():
    session, tagger, deck_text, lookup = _world()
    ev = Evaluator(session, C.EvaluateOptions(tuples=4, hubs=3), fetcher=make_fetcher(session),
                   metas=CardMetaLookup(lookup), role_tagger=tagger, log=lambda m: None)
    ev.telemetry.filter_parse_failed = True  # left over from some earlier run
    ev.telemetry.static_cdn_ignores_query_params = True
    res = ev.run(parse_decklist_text(deck_text))
    assert res.telemetry.filter_parse_failed is False and res.telemetry.static_cdn_ignores_query_params is False
    assert not any("recogni" in w.lower() for w in res.warnings)


# =========================================================================== up to 10 swaps, with confidence tiers


def test_swaps_carry_a_confidence_tier_from_the_strength_of_the_add():
    cuts = []
    for i in range(3):
        c = scores(f"cut{i}", True, meta(f"cut{i}", cmc=3))
        c.s5 = -40.0
        cuts.append(c)
    adds = []
    for name, s5 in (("strong add", 40.0), ("moderate add", 8.0), ("weak add", -5.0)):
        a = scores(name, False, meta(name, cmc=2))
        a.s5 = s5
        adds.append(a)
    swaps = A5.match_swaps(cuts, adds)
    assert {p.add.name: p.confidence for p in swaps} == {"strong add": "strong", "moderate add": "moderate", "weak add": "weak"}


def test_adds_down_to_the_configured_floor_are_eligible_for_swaps():
    assert C.ADD_MIN_S5 == -20.0 and C.MAX_SWAPS == 20

    def ext(name, s5):
        c = scores(name, False, meta(name, price=1.0))
        c.s5, c.finalist = s5, True
        return c

    pool = A5.add_candidates([ext("fine", -15.0), ext("too weak", -25.0), ext("strong", 30.0)], budget=25.0)
    assert {c.name for c in pool} == {"fine", "strong"}


def test_default_weights_lean_on_the_signals_that_recover_the_build_path():
    """Real-corpus ablations (meta/EVALUATE_BACKTEST.md): S1 and S3 carry the signal; S2 (budget-survivor) and S4
    (structure) were worth less than their old weights. Keep that ordering unless a new ablation says otherwise."""
    w = A5.effective_weights(C.DEFAULT_WEIGHTS)
    assert sum(w) == pytest.approx(1.0)
    assert w[0] > w[2] > w[1] > w[3]  # S1 > S3 > S2 > S4
    assert w[0] > 0.4 and w[3] < 0.1


def test_reports_show_each_swaps_confidence(tmp_path):
    from renderers.evaluate_renderer import console_summary, write_reports

    session, tagger, deck_text, lookup = _world()
    ev = Evaluator(session, C.EvaluateOptions(tuples=4, hubs=3), fetcher=make_fetcher(session),
                   metas=CardMetaLookup(lookup), role_tagger=tagger, log=lambda m: None)
    res = ev.run(parse_decklist_text(deck_text))
    assert res.swaps and all(p.confidence in ("strong", "moderate", "weak") for p in res.swaps)
    write_reports(res, str(tmp_path / "r.html"), str(tmp_path / "r.md"), "")
    assert "Confidence" in (tmp_path / "r.html").read_text(encoding="utf-8")
    assert "| Confidence |" in (tmp_path / "r.md").read_text(encoding="utf-8")
    assert f"[{res.swaps[0].confidence}]" in console_summary(res)


def test_within_budget_handles_unlimited_and_constrained():
    cheap = meta("Cheap", price=5.0)
    expensive = meta("Expensive", price=150.0)
    unpriced = CardMeta(name="Unpriced", front="Unpriced", price_known=False)

    # Budget <= 0 or None means unlimited
    for b in (None, 0.0, -10.0):
        assert cheap.within_budget(b) is True
        assert expensive.within_budget(b) is True
        assert unpriced.within_budget(b) is True

    # Constrained budget
    assert cheap.within_budget(25.0) is True
    assert expensive.within_budget(25.0) is False
    assert unpriced.within_budget(25.0) is False


def test_role_tagger_backs_off_on_429():
    from edhparse.evaluate.edhrec import Telemetry
    class RetrySession(FakeSession):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def get(self, *a, **k):
            self.calls += 1
            if self.calls == 1:
                resp = FakeResp(429, "Too Many Requests")
                resp.headers = {"Retry-After": "0.1"}
                return resp
            return FakeResp(200, json.dumps({"data": [{"name": "Cultivate"}]}))

    slept = []
    tel = Telemetry()
    tagger = RoleTagger(RetrySession(), tel, cache=RoleTagCache(":memory:"), sleep=lambda s: slept.append(s))
    res = tagger._search("Cultivate")
    assert res == [{"name": "Cultivate"}]
    assert any("backing off" in line for line in tel.log)
    assert len(slept) >= 1


def test_report_shows_unconstrained_budget_note(tmp_path):
    from renderers.evaluate_renderer import write_reports

    session, tagger, deck_text, lookup = _world()
    ev = Evaluator(session, C.EvaluateOptions(tuples=4, hubs=3, max_add_budget=0), fetcher=make_fetcher(session),
                   metas=CardMetaLookup(lookup), role_tagger=tagger, log=lambda m: None)
    res = ev.run(parse_decklist_text(deck_text))
    assert res.max_add_budget == 0
    write_reports(res, str(tmp_path / "r.html"), "", "")
    html = (tmp_path / "r.html").read_text(encoding="utf-8")
    assert "Adds are unconstrained by budget (any price)." in html



def test_parse_moxfield_default_format_with_sideboard_and_trailing_commander():
    raw = """1 Aesi, Tyrant of Gyre Strait
1 Aetherize
1 Aggressive Biomancy
1 Arcane Signet
1 Ashaya, Soul of the Wild
1 Sol Ring
40 Forest
50 Island

SIDEBOARD:
1 Beanstalk Giant
1 Cultivate
1 Rampant Growth
1 Tireless Tracker

1 Bonny Pall, Clearcutter
"""
    deck = parse_decklist_text(raw)
    assert deck.commanders == ["Bonny Pall, Clearcutter"]
    assert len(deck.sideboard) == 4
    assert {e.name for e in deck.sideboard} == {"Beanstalk Giant", "Cultivate", "Rampant Growth", "Tireless Tracker"}
    assert deck.total == 97
    assert "Bonny Pall, Clearcutter" not in {e.name for e in deck.sideboard}


def test_deck_over_100_cards_supported_and_scales_cuts():
    raw = """Commander
1 Test Commander

Deck
""" + "".join(f"1 Card {i}\n" for i in range(109))
    deck = parse_decklist_text(raw)
    assert deck.total == 110
    assert any("10 over 100" in w for w in deck.warnings)
    assert any("expected 100" in w for w in deck.warnings)


def test_sideboard_cards_evaluated_and_rendered(tmp_path):
    from renderers.evaluate_renderer import write_reports

    session, tagger, deck_text, base_lookup = _world()
    # Add a sideboard card
    deck_with_sb = deck_text + "\nSIDEBOARD:\n1 Budget Gem\n"
    deck = parse_decklist_text(deck_with_sb)
    assert len(deck.sideboard) == 1
    assert deck.sideboard[0].name == "Budget Gem"

    ev = Evaluator(session, C.EvaluateOptions(tuples=4, hubs=3), fetcher=make_fetcher(session),
                   metas=CardMetaLookup(base_lookup), role_tagger=tagger, log=lambda m: None)
    res = ev.run(deck)
    assert len(res.sideboard_scores) == 1
    assert res.sideboard_scores[0].name == "Budget Gem"
    assert "SIDEBOARD" in res.sideboard_scores[0].tags
    write_reports(res, str(tmp_path / "sb.html"), "", "")
    html = (tmp_path / "sb.html").read_text(encoding="utf-8")
    assert "Sideboard &amp; considering evaluation" in html
    assert "tag-sb" in html
