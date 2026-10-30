"""Orchestrates the evaluator: fetch cohorts, triage, score S1-S4, ensemble S5, swaps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from edhparse.evaluate import config as C
from edhparse.evaluate.algorithms import anchor_bagging as A1
from edhparse.evaluate.algorithms import ensemble as A5
from edhparse.evaluate.algorithms import hub_webbing as A3
from edhparse.evaluate.algorithms import structural_fit as A4
from edhparse.evaluate.algorithms import survivor_diff as A2
from edhparse.evaluate.algorithms.triage import Triage, triage_deck
from edhparse.evaluate.edhrec import (
    EdhrecFetcher,
    Telemetry,
    average_deck_url,
    card_hub_url,
    commander_slug,
    commander_url,
    edhrec_slug,
    filtered_page_url,
    merge_cohorts,
    parse_filtered_html,
    parse_json_page,
)
from edhparse.evaluate.models import CardMeta, CardScores, Cohort, Decklist, LandUpgrade, SwapPair, name_key
from edhparse.evaluate.scryfall_meta import CardMetaLookup, RoleTagger, edhrec_display_name


class EvaluationError(RuntimeError):
    pass


@dataclass
class EvaluationResult:
    commanders: List[str]
    slug: str
    color_identity: Tuple[str, ...]
    n0: int
    cohort_sizes: Dict[str, int]
    telemetry: Telemetry
    triage: Triage
    tuples: List[A1.TupleCohort]
    hubs: List[Tuple[str, int]]
    exclusions: List[str]
    structure: A4.StructuralModel
    weights: Tuple[float, ...]
    deck_scores: List[CardScores]
    candidate_scores: List[CardScores]
    cuts: List[CardScores]
    adds: List[CardScores]
    swaps: List[SwapPair]
    land_upgrades: List[LandUpgrade]
    warnings: List[str] = field(default_factory=list)
    max_add_budget: float = C.DEFAULT_MAX_ADD_BUDGET
    deck_total: int = 100
    sideboard_scores: List[CardScores] = field(default_factory=list)


def _ci_subset(meta: CardMeta, ci: Sequence[str]) -> bool:
    return set(meta.color_identity) <= set(ci)


def resolve_commanders(names: Sequence[str], metas: CardMetaLookup) -> List[CardMeta]:
    out: List[CardMeta] = []
    for raw in names:
        meta = metas.get(raw)
        if meta.found:
            out.append(meta)
            continue
        parts = [p.strip() for p in raw.split(",")]
        resolved = False
        for i in range(1, len(parts)):
            left, right = ", ".join(parts[:i]), ", ".join(parts[i:])
            ml, mr = metas.get(left), metas.get(right)
            if ml.found and mr.found:
                out.extend([ml, mr])
                resolved = True
                break
        if not resolved:
            raise EvaluationError(f"Commander not found in Scryfall data: {raw}")
    return out[:2]


def _avg_deck_names(raw: Optional[dict]) -> List[str]:
    if not raw:
        return []
    deck = raw.get("deck") or {}
    names: List[str] = []
    for _, cards in (deck.get("cards") or {}).items():
        for entry in cards:
            if isinstance(entry, (list, tuple)) and entry:
                names.extend([entry[0]] * int(entry[1] if len(entry) > 1 else 1))
    if names:
        return names
    jd = ((raw.get("container") or {}).get("json_dict") or {})
    for cl in jd.get("cardlists") or []:
        for v in cl.get("cardviews") or []:
            if v.get("name"):
                names.append(v["name"])
    return names


class Evaluator:
    def __init__(self, session, options: C.EvaluateOptions, *, fetcher: Optional[EdhrecFetcher] = None,
                 metas: Optional[CardMetaLookup] = None, role_tagger: Optional[RoleTagger] = None,
                 log: Callable[[str], None] = print):
        self.opts = options
        self.telemetry = fetcher.telemetry if fetcher else Telemetry()
        self.fetcher = fetcher or EdhrecFetcher(
            session, max_requests=options.max_requests, ttl_days=options.cache_ttl_days, telemetry=self.telemetry
        )
        self.metas = metas or CardMetaLookup()
        self.role_tagger = role_tagger or RoleTagger(session, self.telemetry)
        self.log = log

    # ----------------------------------------------------------------- fetching
    def _json(self, url: str, label: str, source: str = "json", keep: int = 0) -> Optional[Cohort]:
        data = self.fetcher.fetch(url, "json", keep=keep)
        if not isinstance(data, dict):
            return None
        return parse_json_page(data, label, url, source)

    def _baseline(self, slug: str, commanders: Sequence[CardMeta]) -> Cohort:
        d0 = self._json(commander_url(slug), "Commander baseline (D0)")
        if len(commanders) == 2 and (d0 is None or d0.num_decks < 15):
            singles = [self._json(commander_url(edhrec_slug(m.front)), f"{m.front}") for m in commanders]
            singles = [s for s in singles if s is not None]
            if singles:
                self.log("Pair page missing or tiny; merging both partners' pages.")
                d0 = merge_cohorts("Merged partner baseline (D0)", singles)
        if d0 is None or d0.num_decks <= 0:
            raise EvaluationError(f"No EDHREC commander page found for '{slug}'.")
        return d0

    def _tuple_cohorts(self, slug: str, pairs: Sequence[Tuple[str, str]], exclusions: Sequence[str],
                       d0: Cohort, anchors: Sequence[str], reserve: int = 0) -> List[A1.TupleCohort]:
        excl_slugs = [d0.get(e).slug if d0.get(e) else edhrec_slug(e) for e in exclusions]
        results: List[A1.TupleCohort] = []
        pending: List[Tuple[str, str]] = []
        tier_a_ok = True
        for pair in pairs:
            inc = [d0.get(a).slug if d0.get(a) else edhrec_slug(a) for a in pair]
            url = filtered_page_url(slug, inc, excl_slugs)
            if not tier_a_ok or self.fetcher.is_blocked(url):
                pending.append(pair)
                continue
            html = self.fetcher.fetch(url, "html", keep=reserve)
            if html is None:
                if (self.fetcher.is_blocked(url) or self.telemetry.budget_exhausted
                        or not self.fetcher.has_slack(reserve)):
                    tier_a_ok = False
                    pending.append(pair)
                else:
                    self.telemetry.note(f"filter page unavailable for {pair}; skipped")
                continue
            cohort = parse_filtered_html(html, " + ".join(pair), url, slug)
            if cohort.num_decks <= 0 or not cohort.cards:
                if cohort.num_decks == 0 and "numDecks" not in html:
                    # Unparseable page: treat like a missing filter backend.
                    tier_a_ok = False
                    pending.append(pair)
                    self.telemetry.filter_parse_failed = True
                    self.telemetry.note("filtered page had no recognisable data; falling back to theme pages")
                else:
                    self.telemetry.note(f"no decks run both {pair[0]} and {pair[1]}; tuple skipped")
                continue
            same_n = abs(cohort.num_decks - d0.num_decks) <= max(1, 0.001 * d0.num_decks)
            if same_n and cohort.top_cards(3) == d0.top_cards(3):
                self.telemetry.static_cdn_ignores_query_params = True
                tier_a_ok = False
                pending.append(pair)
                self.telemetry.note("filtered page identical to baseline (query ignored); switching to theme pages")
                continue
            results.append(A1.TupleCohort(anchors=tuple(pair), cohort=cohort, n_eff=cohort.num_decks, tier="A",
                                          exclusions=tuple(exclusions)))
        if pending:
            results.extend(self._theme_cohorts(slug, pending, d0, anchors, reserve))
        return results

    def _theme_cohorts(self, slug: str, pairs: Sequence[Tuple[str, str]], d0: Cohort,
                       anchors: Sequence[str], reserve: int = 0) -> List[A1.TupleCohort]:
        links = ((d0.raw or {}).get("panels") or {}).get("taglinks") or []
        links = sorted(links, key=lambda t: -(t.get("count") or 0))[: C.MAX_THEME_PAGES]
        themes: List[Tuple[str, Cohort]] = []
        for link in links:
            tslug = link.get("slug")
            if not tslug:
                continue
            cohort = self._json(commander_url(slug, tslug), f"Theme: {link.get('value') or tslug}", "theme", keep=reserve)
            if cohort is not None and cohort.num_decks > 0:
                themes.append((tslug, cohort))
        out = []
        for pair in pairs:
            best = A1.best_theme_for(pair, themes, d0)
            if best is None:
                continue
            tslug, theme, _score = best
            affinity = min(2.0, max(0.25, A1.theme_affinity(theme, anchors, d0)))
            out.append(A1.TupleCohort(anchors=tuple(pair), cohort=theme, n_eff=theme.num_decks, tier="B",
                                      theme=tslug, affinity=affinity))
        return out

    # ----------------------------------------------------------------- stages
    def _fetch_base(self, slug: str, cmd_metas: Sequence[CardMeta], warnings: List[str]):
        d0 = self._baseline(slug, cmd_metas)
        d_high = self._json(commander_url(slug, "expensive"), "Expensive decks (D_high)")
        d_low = self._json(commander_url(slug, "budget"), "Budget decks (D_low)")
        avg_raw = self.fetcher.fetch(average_deck_url(slug, "expensive"), "json")
        if not isinstance(avg_raw, dict):
            avg_raw = self.fetcher.fetch(average_deck_url(slug), "json")
        if d_high is None or d_low is None:
            warnings.append("Budget/expensive pages unavailable; S2 is neutral for every card.")
        self.log(f"Baseline: {d0.num_decks} decks; expensive {d_high.num_decks if d_high else '-'}; "
                 f"budget {d_low.num_decks if d_low else '-'}")
        return d0, d_high, d_low, avg_raw

    def _triage(self, deck: Decklist, d0: Cohort, cmd_keys: set, warnings: List[str]):
        metas: Dict[str, CardMeta] = {}
        deck_names: List[str] = []
        land_metas: List[CardMeta] = []
        for e in (e for e in deck.cards if name_key(e.name) not in cmd_keys):
            meta = self.metas.get(e.name)
            if not meta.found:
                warnings.append(f"Not found in Scryfall data (ignored): {e.name}")
                continue
            metas[name_key(e.name)] = meta
            if name_key(meta.name) != name_key(e.name):
                metas[name_key(meta.name)] = meta
            if name_key(meta.front) != name_key(e.name):
                metas[name_key(meta.front)] = meta
            deck_names.append(e.name)
            if meta.is_land:
                land_metas.extend([meta] * max(1, e.quantity))
        tri = triage_deck(deck_names, metas, d0)
        self.log(f"Triage: {len(tri.basic_lands) + len(tri.utility_lands)} lands, {len(tri.staples)} staples, "
                 f"{len(tri.anchors)} anchors, {len(tri.niche)} niche")
        return metas, land_metas, tri

    def _build_s1(self, slug: str, ctx: "_Ctx", cmd_keys: set, warnings: List[str]):
        """Anchor-tuple cohorts; returns (exclusions, S1 weight factor) and fills ``ctx.tuples``."""
        opts, d0, tri = self.opts, ctx.d0, ctx.tri
        pairs = A1.select_tuples(tri.anchors, ctx.metas, d0, opts.tuples)
        low_lands = {name_key(n) for n in (ctx.d_low.names() if ctx.d_low else []) if self.metas.get(n).is_land}
        exclusions = A1.precon_exclusions(ctx.d_low, ctx.d_high, list(ctx.metas.keys()) + list(cmd_keys), low_lands)
        hub_reserve = min(C.MIN_HUB_BUDGET_RESERVE, opts.hubs) if tri.anchors else 0
        ctx.tuples = self._tuple_cohorts(slug, pairs, exclusions, d0, tri.anchors, hub_reserve) if pairs else []
        if self.telemetry.filter_parse_failed:
            warnings.append("EDHREC filter pages returned no recognisable data (site layout may have changed); "
                            "S1 is using theme pages instead.")
        tiers = {t.tier for t in ctx.tuples}
        if "A" in tiers:
            self.telemetry.filter_mode = "Tier A: live EDHREC 'with card' filters" + (" (+ theme fallback)" if "B" in tiers else "")
            return exclusions, 1.0
        if "B" in tiers:
            self.telemetry.filter_mode = "Tier B: commander theme pages (S1 weight halved)"
            return exclusions, C.THEME_FALLBACK_S1_FACTOR
        self.telemetry.filter_mode = "none: S1 disabled"
        warnings.append("No anchor cohorts could be built; S1 is disabled.")
        return exclusions, 0.0

    def _build_s3(self, ctx: "_Ctx", warnings: List[str]) -> None:
        hub_names = A3.select_hubs(ctx.tri.anchors, ctx.metas, ctx.d0,
                                   lambda n: (ctx.bag(n).e_context, ctx.bag(n).bag_lift), self.opts.hubs)
        for h in hub_names:
            stat = ctx.d0.get(h)
            hslug = stat.slug if stat else edhrec_slug(edhrec_display_name(ctx.metas[name_key(h)]))
            cohort = self._json(card_hub_url(hslug), f"Hub: {h}", "hub")
            if cohort is not None:
                ctx.hubs.append((h, cohort))
        if not ctx.hubs:
            warnings.append("No engine hub pages available; S3 is disabled.")

    def _candidate_pool(self, ctx: "_Ctx", cmd_keys: set, ci: Sequence[str], sideboard: Sequence[DeckEntry] = ()):
        """Cards seen on any fetched page or in sideboard that could legally join the deck: (non-lands, [(land, price)])."""
        d0, d_high, d_low = ctx.d0, ctx.d_high, ctx.d_low
        deck_keys = set(ctx.metas.keys()) | cmd_keys
        sources: List[Cohort] = [c for c in (d0, d_high, d_low) if c is not None]
        sources += [t.cohort for t in ctx.tuples if t.cohort is not None]
        cand_names: Dict[str, str] = {}
        for c in sources + [h for _, h in ctx.hubs]:
            for n in c.names():
                k = name_key(n)
                if k in deck_keys or k in cand_names:
                    continue
                cand_names[k] = n
        for e in sideboard:
            k = name_key(e.name)
            if k not in deck_keys and k not in cand_names:
                cand_names[k] = e.name

        candidates: List[str] = []
        land_candidates: List[Tuple[str, float]] = []
        sb_keys = {name_key(e.name) for e in sideboard}
        for k, n in cand_names.items():
            meta = self.metas.get(n)
            if not meta.found or not meta.legal or not _ci_subset(meta, ci):
                continue
            if name_key(meta.name) in deck_keys or name_key(meta.front) in deck_keys:
                continue
            ctx.metas[k] = meta
            if meta.is_land:
                if not meta.is_basic and (k in sb_keys or (meta.within_budget(self.opts.max_add_budget) and (d0.present(n) or (d_high and d_high.present(n))))):
                    land_candidates.append((n, meta.price))
                continue
            candidates.append(n)
        self.log(f"Candidates: {len(candidates)} non-land, {len(land_candidates)} lands")
        return candidates, land_candidates

    @staticmethod
    def _score_card(ctx: "_Ctx", name: str, in_deck: bool) -> CardScores:
        """Raw S1-S3 for one card (S4 needs role tags and is filled in later)."""
        meta = ctx.metas[name_key(name)]
        b = ctx.bag(name)
        s = A2.survivor_score(name, meta, ctx.d_high, ctx.d_low)
        h = A3.hub_score(name, ctx.hubs)
        cs = CardScores(name=meta.name if not in_deck else name, in_deck=in_deck, meta=meta,
                        pool=ctx.tri.pool.get(name_key(name), "X"), p_raw_d0=ctx.d0.p_raw(name),
                        lift_d0=ctx.d0.lift(name), e_context=b.e_context, bag_lift=b.bag_lift, orr=s.orr,
                        k_hubs=h.k_hubs)
        cs.raw = [b.raw, s.raw, h.raw, 0.0]
        cs.protected = name_key(name) in ctx.protected or name_key(meta.name) in ctx.protected
        is_sb = (not in_deck) and (name_key(name) in ctx.sideboard_keys or name_key(meta.name) in ctx.sideboard_keys)
        cs.from_sideboard = is_sb
        if is_sb and "SIDEBOARD" not in cs.tags:
            cs.tags.append("SIDEBOARD")
        return cs

    @staticmethod
    def _prescreen(deck_scores: List[CardScores], ext_scores: List[CardScores],
                   weights: Sequence[float], budget: float) -> List[CardScores]:
        """Shortlist externals on S1-S3 only (S4 needs role tags, which cost requests).

        Only cards that could actually be recommended (priced, within ``budget``) compete for the
        finalist slots; unbuyable ones would just be dropped from the report later.
        """
        frames = [A5.reference_frame([c.raw[k] for c in deck_scores], C.MIN_SIGNAL_SCALES[k]) for k in range(3)]
        for c in ext_scores:
            c.finalist = False
            c.prelim_score = sum(weights[k] * A5.project(c.raw[k], *frames[k]) for k in range(3))
        sb_finalists = [c for c in ext_scores if c.from_sideboard or "SIDEBOARD" in c.tags]
        for c in sb_finalists:
            c.finalist = True
        affordable = [c for c in ext_scores if c.meta.within_budget(budget) and not (c.from_sideboard or "SIDEBOARD" in c.tags)]
        affordable.sort(key=lambda c: c.prelim_score, reverse=True)
        rem = max(0, C.FINALISTS - len(sb_finalists))
        finalists = sb_finalists + affordable[:rem]
        for c in finalists:
            c.finalist = True
        return finalists

    def _score_structure(self, ctx: "_Ctx", deck_scores: List[CardScores], finalists: List[CardScores],
                         land_metas: Sequence[CardMeta], avg_raw) -> A4.StructuralModel:
        avg_metas = [self.metas.get(n) for n in _avg_deck_names(avg_raw)]
        avg_metas = [m for m in avg_metas if m.found]
        # The average deck is tagged too: its role counts set this commander's role floors.
        self.role_tagger.tag([c.meta for c in deck_scores] + [c.meta for c in finalists] + avg_metas)
        structure = A4.build_model([c.meta for c in deck_scores], land_metas, avg_metas, ctx.d0)
        for c in deck_scores + finalists:
            c.slot_pressure = structure.slot_pressure(c.meta)
            c.role_boost = structure.role_boost(c.meta)
            c.raw[3] = -c.slot_pressure + c.role_boost
        for c in deck_scores:
            scarce = structure.scarce_roles_if_cut(c.meta)
            c.scarce_roles = scarce
            c.requires_same_role_replacement = bool(scarce)
        return structure

    # ----------------------------------------------------------------- main
    def run(self, deck: Decklist) -> EvaluationResult:
        opts = self.opts
        warnings = list(deck.warnings)
        # Per-run flags; fetcher-level state (blocked hosts, request counts) deliberately persists.
        self.telemetry.filter_parse_failed = False
        self.telemetry.static_cdn_ignores_query_params = False
        self.telemetry.filter_mode = ""
        if not deck.commanders:
            raise EvaluationError("No commander identified (use --commander).")
        cmd_metas = resolve_commanders(deck.commanders, self.metas)
        ci = tuple(sorted({c for m in cmd_metas for c in m.color_identity}))
        slug = commander_slug([m.front for m in cmd_metas])
        cmd_keys = {name_key(m.name) for m in cmd_metas} | {name_key(m.front) for m in cmd_metas}
        self.log(f"Commander: {' + '.join(m.name for m in cmd_metas)}  (EDHREC slug: {slug})")

        d0, d_high, d_low, avg_raw = self._fetch_base(slug, cmd_metas, warnings)
        metas, land_metas, tri = self._triage(deck, d0, cmd_keys, warnings)
        ctx = _Ctx(d0=d0, d_high=d_high, d_low=d_low, metas=metas, tri=tri,
                   protected=deck.protected_names(), sideboard_keys=deck.sideboard_names())

        exclusions, s1_factor = self._build_s1(slug, ctx, cmd_keys, warnings)
        self._build_s3(ctx, warnings)
        candidates, land_candidates = self._candidate_pool(ctx, cmd_keys, ci, deck.sideboard)

        deck_scores = [self._score_card(ctx, n, True) for n in tri.nonland]
        ext_scores = [self._score_card(ctx, n, False) for n in candidates]
        if not deck_scores:
            raise EvaluationError("No non-land cards in the deck could be scored.")

        effective = A5.effective_weights(opts.weights, s1_factor, bool(ctx.hubs))
        finalists = self._prescreen(deck_scores, ext_scores, effective, opts.max_add_budget)
        structure = self._score_structure(ctx, deck_scores, finalists, land_metas, avg_raw)

        A5.normalise_scores(deck_scores + finalists)
        A5.apply_s5(deck_scores + finalists, effective)
        swaps = A5.match_swaps(A5.cut_candidates(deck_scores), A5.add_candidates(finalists, opts.max_add_budget))
        cut_count = C.REPORT_CUTS
        if deck.total > 100:
            cut_count = max(C.REPORT_CUTS, (deck.total - 100) + 5)
        report_cuts = sorted([c for c in deck_scores if not c.protected], key=lambda c: c.s5)[: cut_count]
        report_adds = sorted([c for c in finalists if c.meta.within_budget(opts.max_add_budget)], key=lambda c: c.s5,
                             reverse=True)[: C.REPORT_ADDS]
        sideboard_scores = sorted([c for c in finalists if c.from_sideboard or "SIDEBOARD" in c.tags], key=lambda c: c.s5, reverse=True)
        lands = A5.land_upgrades(tri.utility_lands, land_candidates, d0, d_high)

        sizes = {"D0": d0.num_decks}
        if d_high:
            sizes["D_high"] = d_high.num_decks
        if d_low:
            sizes["D_low"] = d_low.num_decks
        self.log(f"EDHREC requests: {self.telemetry.edhrec_requests} (cache hits {self.telemetry.edhrec_cache_hits}); "
                 f"Scryfall tag requests: {self.telemetry.scryfall_requests}")
        return EvaluationResult(
            commanders=[m.name for m in cmd_metas], slug=slug, color_identity=ci, n0=d0.num_decks,
            cohort_sizes=sizes, telemetry=self.telemetry, triage=tri, tuples=ctx.tuples,
            hubs=[(h, c.num_decks) for h, c in ctx.hubs], exclusions=exclusions, structure=structure,
            weights=effective, deck_scores=deck_scores, candidate_scores=finalists, cuts=report_cuts,
            adds=report_adds, swaps=swaps, land_upgrades=lands, warnings=warnings,
            max_add_budget=opts.max_add_budget,
            deck_total=deck.total,
            sideboard_scores=sideboard_scores,
        )


@dataclass
class _Ctx:
    """Fetched data shared by the scoring stages of one evaluation run."""

    d0: Cohort
    d_high: Optional[Cohort]
    d_low: Optional[Cohort]
    metas: Dict[str, CardMeta]
    tri: Triage
    protected: set
    sideboard_keys: set = field(default_factory=set)
    tuples: List[A1.TupleCohort] = field(default_factory=list)
    hubs: List[Tuple[str, Cohort]] = field(default_factory=list)

    def bag(self, name: str) -> A1.BagScore:
        return A1.bag_score(name, self.tuples, self.d0)
