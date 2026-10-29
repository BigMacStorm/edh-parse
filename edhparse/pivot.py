"""
Pivot tool: suggest alternate commanders based on expensive cards already in a deck.

For each card priced at/above a threshold, pull EDHREC "Top Commanders" and aggregate
inclusion percentages weighted by card price.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.deck import EDHREC_SLEEP, _edhrec_get_with_retry

from edhparse.edhrec_util import edhrec_card_slug, inclusion_pct

# Re-export for callers that imported from this module
__all__ = [
    "PricedCard",
    "CommanderHit",
    "CardContribution",
    "CommanderAggregate",
    "edhrec_card_slug",
    "extract_top_commanders",
    "fetch_top_commanders_for_card",
    "aggregate_commander_scores",
    "select_expensive_cards",
    "enrich_commanders_with_scryfall",
    "build_pivot_report",
]


@dataclass
class PricedCard:
    name: str
    price: float
    quantity: int = 1
    type_line: str = ""


@dataclass
class CommanderHit:
    name: str
    slug: str
    inclusion_pct: float


@dataclass
class CardContribution:
    name: str
    price: float
    inclusion_pct: float
    quantity: int = 1

    @property
    def weighted_score(self) -> float:
        return float(self.price) * (float(self.inclusion_pct) / 100.0) * max(1, int(self.quantity))


@dataclass
class CommanderAggregate:
    name: str
    slug: str
    cards: List[CardContribution] = field(default_factory=list)
    weighted_score: float = 0.0
    retained_value: float = 0.0

    def finalize(self) -> None:
        self.cards.sort(key=lambda c: (-c.weighted_score, -c.price, c.name.lower()))
        self.weighted_score = sum(c.weighted_score for c in self.cards)
        self.retained_value = sum(c.price * max(1, c.quantity) for c in self.cards)


def extract_top_commanders(card_page: dict) -> List[CommanderHit]:
    """Pull Top Commanders cardviews from an EDHREC card-page JSON payload."""
    container = card_page.get("container") or {}
    json_dict = container.get("json_dict") or {}
    cardlists = json_dict.get("cardlists") or []

    hits: List[CommanderHit] = []
    for section in cardlists:
        header = (section.get("header") or "").strip().lower()
        if header != "top commanders":
            continue
        for cv in section.get("cardviews") or []:
            name = (cv.get("name") or "").strip()
            if not name:
                continue
            pct = inclusion_pct(cv)
            if pct is None:
                continue
            slug = (cv.get("slug") or cv.get("sanitized") or edhrec_card_slug(name)).strip()
            hits.append(CommanderHit(name=name, slug=slug, inclusion_pct=float(pct)))
        break
    return hits


def fetch_top_commanders_for_card(session, card_name: str) -> Tuple[List[CommanderHit], Optional[str]]:
    """
    GET json.edhrec.com/pages/cards/{slug}.json and return Top Commanders.

    Returns (hits, error_message). error_message is None on success (including empty list).
    """
    slug = edhrec_card_slug(card_name)
    if not slug:
        return [], f"Could not slugify card name: {card_name!r}"

    url = f"https://json.edhrec.com/pages/cards/{slug}.json"
    response, err = _edhrec_get_with_retry(session, url, timeout=10)
    if err or response is None:
        return [], err or f"No response for {url}"
    if not getattr(response, "from_cache", True):
        time.sleep(EDHREC_SLEEP)
    try:
        data = response.json()
    except Exception as exc:
        return [], f"Invalid JSON for {url}: {exc}"

    return extract_top_commanders(data), None


def aggregate_commander_scores(
    card_commanders: Sequence[Tuple[PricedCard, Sequence[CommanderHit]]],
    *,
    exclude_commander_names: Optional[Iterable[str]] = None,
    min_inclusion_pct: float = 0.0,
) -> List[CommanderAggregate]:
    """
    Aggregate per-card Top Commander hits into ranked commander suggestions.

    Score for a commander is sum(price * inclusion_pct/100 * qty) across contributing cards.
    Retained value is the sum of those cards' prices (qty-aware).
    """
    exclude = {(n or "").strip().lower() for n in (exclude_commander_names or []) if n}
    by_key: Dict[str, CommanderAggregate] = {}

    for card, hits in card_commanders:
        for hit in hits:
            if hit.inclusion_pct < min_inclusion_pct:
                continue
            key = (hit.slug or edhrec_card_slug(hit.name)).lower()
            if not key:
                continue
            if hit.name.strip().lower() in exclude or key in exclude:
                continue

            agg = by_key.get(key)
            if agg is None:
                agg = CommanderAggregate(name=hit.name, slug=hit.slug or edhrec_card_slug(hit.name))
                by_key[key] = agg

            # One contribution per card name per commander (keep highest inclusion if dupes).
            existing = next((c for c in agg.cards if c.name.lower() == card.name.lower()), None)
            contrib = CardContribution(
                name=card.name,
                price=float(card.price),
                inclusion_pct=float(hit.inclusion_pct),
                quantity=max(1, int(card.quantity)),
            )
            if existing is None:
                agg.cards.append(contrib)
            elif hit.inclusion_pct > existing.inclusion_pct:
                existing.inclusion_pct = float(hit.inclusion_pct)
                existing.price = float(card.price)
                existing.quantity = max(1, int(card.quantity))

    results = list(by_key.values())
    for agg in results:
        agg.finalize()
    results.sort(key=lambda a: (-a.weighted_score, -a.retained_value, -len(a.cards), a.name.lower()))
    return results


def select_expensive_cards(
    priced_cards: Sequence[PricedCard],
    *,
    min_price: float = 5.0,
    skip_lands: bool = True,
) -> List[PricedCard]:
    """Filter to cards at/above min_price (optionally skipping lands), sorted by price desc."""
    selected: List[PricedCard] = []
    for card in priced_cards:
        if float(card.price) < float(min_price):
            continue
        type_line = (card.type_line or "").lower()
        if skip_lands and "land" in type_line and "creature" not in type_line:
            continue
        selected.append(card)
    selected.sort(key=lambda c: (-c.price, c.name.lower()))
    return selected


def _image_from_scryfall_data(data: Optional[dict]) -> str:
    """Return a Scryfall image URL from card JSON, preferring large/normal."""
    if not isinstance(data, dict):
        return ""
    image_uris = data.get("image_uris") or {}
    for key in ("large", "normal", "png", "small"):
        if image_uris.get(key):
            return str(image_uris[key])
    for face in data.get("card_faces") or []:
        if not isinstance(face, dict):
            continue
        face_images = face.get("image_uris") or {}
        for key in ("large", "normal", "png", "small"):
            if face_images.get(key):
                return str(face_images[key])
    return ""


def enrich_commanders_with_scryfall(commander_rows: List[Dict[str, Any]]) -> None:
    """Attach card_pic / type_line / oracle_text from local Scryfall bulk onto commander rows."""
    from clients import scryfall_bulk

    for row in commander_rows:
        name = row.get("name") or ""
        data = scryfall_bulk.get_card_by_name_or_face(name)
        if not data:
            row.setdefault("card_pic", "")
            row.setdefault("type_line", "")
            row.setdefault("oracle_text", "")
            continue
        row["card_pic"] = _image_from_scryfall_data(data)
        row["type_line"] = data.get("type_line") or ""
        # Prefer front-face oracle text for DFCs.
        oracle = data.get("oracle_text") or ""
        if not oracle:
            faces = data.get("card_faces") or []
            if faces and isinstance(faces[0], dict):
                oracle = faces[0].get("oracle_text") or ""
        row["oracle_text"] = oracle


def build_pivot_report(
    *,
    source_label: str,
    expensive_cards: Sequence[PricedCard],
    aggregates: Sequence[CommanderAggregate],
    excluded_commander: Optional[str] = None,
    min_price: float = 5.0,
    top_n: Optional[int] = None,
) -> Dict[str, Any]:
    """Assemble a JSON-serializable report structure for the HTML renderer."""
    total_expensive_value = sum(c.price * max(1, c.quantity) for c in expensive_cards)
    ranked = list(aggregates)
    if top_n is not None and top_n > 0:
        ranked = ranked[:top_n]

    commander_rows = []
    for agg in ranked:
        retained_pct = (
            (100.0 * agg.retained_value / total_expensive_value) if total_expensive_value > 0 else 0.0
        )
        avg_inclusion = (
            sum(c.inclusion_pct for c in agg.cards) / len(agg.cards) if agg.cards else 0.0
        )
        commander_rows.append(
            {
                "name": agg.name,
                "slug": agg.slug,
                "weighted_score": agg.weighted_score,
                "retained_value": agg.retained_value,
                "retained_pct": retained_pct,
                "card_count": len(agg.cards),
                "avg_inclusion_pct": avg_inclusion,
                "card_pic": "",
                "type_line": "",
                "oracle_text": "",
                "cards": [
                    {
                        "name": c.name,
                        "price": c.price,
                        "inclusion_pct": c.inclusion_pct,
                        "quantity": c.quantity,
                        "weighted_score": c.weighted_score,
                    }
                    for c in agg.cards
                ],
            }
        )

    enrich_commanders_with_scryfall(commander_rows)

    return {
        "source_label": source_label,
        "excluded_commander": excluded_commander or "",
        "min_price": float(min_price),
        "expensive_card_count": len(expensive_cards),
        "total_expensive_value": total_expensive_value,
        "commander_count": len(commander_rows),
        "expensive_cards": [
            {
                "name": c.name,
                "price": c.price,
                "quantity": c.quantity,
                "type_line": c.type_line,
            }
            for c in expensive_cards
        ],
        "commanders": commander_rows,
    }
