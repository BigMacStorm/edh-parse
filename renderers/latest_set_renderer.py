import time
from itertools import chain
from typing import Any, Callable, Dict, List, Optional

from utils.html_render_utils import _escape


def write_latest_set_html(
    decks: List[Any],
    session: Any,
    path: str = "outputs/latest_set.html",
    get_with_retry_fn: Optional[Callable[..., Any]] = None,
    scryfall_sleep: float = 0.15,
) -> None:
    """Write a webpage highlighting cards from the most recent set across all decks."""

    if get_with_retry_fn is None:
        raise ValueError("get_with_retry_fn is required")

    decks_with_commander = [d for d in decks if getattr(d, "commander", None) is not None]
    if not decks_with_commander:
        return

    # Determine the latest real set globally from Scryfall (e.g. newest expansion like TMT),
    # then find cards in your decks that are truly new to that set.
    allowed_set_types = {"expansion", "core", "commander", "draft_innovation"}

    # Gather all cards from all decks once.
    all_cards = []
    for deck in decks_with_commander:
        cards = [deck.commander] + list(chain(deck.mainboard, deck.sideboard))
        for card in cards:
            if not card or getattr(card, "error", False):
                continue
            all_cards.append((deck, card))

    # Group "new set" cards per commander, but only keep cards that are truly new
    # (their first printing is in this latest set date).
    earliest_release_cache: Dict[str, Optional[Any]] = {}

    def is_first_print_in_set(card: Any, set_code: str) -> bool:
        """Return True if the earliest printing of this card is from the given set code."""
        if not card or not getattr(card, "name", None):
            return False
        key = card.name
        if key in earliest_release_cache:
            earliest, earliest_set = earliest_release_cache[key]
        else:
            # Use Scryfall API to inspect all printings for this card by exact name.
            url = "https://api.scryfall.com/cards/named"
            params = {"exact": card.name}
            response, err = get_with_retry_fn(session, url, params=params, timeout=5)
            if err:
                earliest_release_cache[key] = (None, None)
                return False
            if not getattr(response, "from_cache", True):
                time.sleep(scryfall_sleep)
            data = response.json()
            prints_uri = data.get("prints_search_uri")
            if not prints_uri:
                earliest_release_cache[key] = (None, None)
                return False
            response2, err2 = get_with_retry_fn(session, prints_uri, timeout=15)
            if err2:
                earliest_release_cache[key] = (None, None)
                return False
            if not getattr(response2, "from_cache", True):
                time.sleep(scryfall_sleep)
            data2 = response2.json()
            earliest = None
            earliest_set = None
            for printing in data2.get("data", []):
                d = printing.get("released_at")
                scode = printing.get("set")
                if not d or not scode:
                    continue
                d_str = str(d)
                if earliest is None or d_str < earliest:
                    earliest = d_str
                    earliest_set = scode
            earliest_release_cache[key] = (earliest, earliest_set) if earliest else (None, None)

        earliest, earliest_set = earliest_release_cache.get(key, (None, None))
        return earliest is not None and earliest_set == set_code

    # Ask Scryfall for sets ordered by release date, newest first, and
    # pick the newest set that (a) is a real set type and (b) has at least
    # one card in our decks that is truly new to that set.
    target_set_code = None
    target_set_name = None
    target_released_at = None

    sets_url = "https://api.scryfall.com/sets"
    params = {"order": "released", "dir": "desc"}
    response, err = get_with_retry_fn(session, sets_url, params=params, timeout=10)
    if err:
        print(f"Error fetching Scryfall sets: {err}")
        return
    if not getattr(response, "from_cache", True):
        time.sleep(scryfall_sleep)
    sets_data = response.json()
    for s in sets_data.get("data", []):
        stype = s.get("set_type")
        if stype not in allowed_set_types:
            continue
        candidate_code = s.get("code")
        candidate_name = s.get("name")
        candidate_released_at = s.get("released_at")
        # Filter cards actually from this set.
        candidate_cards = [
            c for (_, c) in all_cards if getattr(c, "set_code", None) == candidate_code
        ]
        if not candidate_cards:
            continue
        # See if any of these cards are truly first printed in this set.
        has_new = any(
            is_first_print_in_set(card, candidate_code) for card in candidate_cards
        )
        if has_new:
            target_set_code = candidate_code
            target_set_name = candidate_name
            target_released_at = candidate_released_at
            break

    if not target_set_code:
        print("No cards from any recent set found in any deck.")
        return

    # Group per commander.
    by_commander: Dict[str, Dict[str, Any]] = {}
    for deck, card in all_cards:
        if getattr(card, "set_code", None) != target_set_code:
            continue
        if not is_first_print_in_set(card, target_set_code):
            continue
        cname = deck.commander.name or "Unknown Commander"
        by_commander.setdefault(cname, {"deck": deck, "cards": []})
        by_commander[cname]["cards"].append(card)

    if not by_commander:
        print("No cards from the most recent set found in any deck.")
        return

    set_label = target_set_name or target_set_code

    html_parts = [
        """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Newest Cards by Commander</title>
<style>
:root { --bg: #0f0f14; --surface: #1c1e26; --card: #252830; --text: #e4e4e7; --muted: #6b7280; --accent: #7c3aed; --green: #22c55e; --amber: #f59e0b; }
* { box-sizing: border-box; }
body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 1.5rem 2rem; line-height: 1.5; }
h1 { font-size: 1.75rem; margin-bottom: 0.25rem; color: var(--text); font-weight: 600; }
.subtitle { font-size: 0.9rem; color: var(--muted); margin-bottom: 1.5rem; }
.commander-section { background: var(--surface); border-radius: 12px; padding: 1.25rem; margin-bottom: 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.3); }
.commander-header { display: flex; align-items: flex-start; gap: 1rem; flex-wrap: wrap; margin-bottom: 1rem; }
.commander-pic { width: 200px; height: 280px; object-fit: contain; border-radius: 8px; border: 1px solid var(--card); }
.commander-meta { flex: 1; min-width: 180px; }
.commander-meta h2 { margin: 0 0 0.4rem; font-size: 1.2rem; }
.commander-meta p { margin: 0.1rem 0; font-size: 0.85rem; color: var(--muted); }
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 0.75rem; margin-top: 0.5rem; }
.card-tile { background: var(--card); border-radius: 10px; padding: 0.6rem; display: flex; flex-direction: column; gap: 0.3rem; }
.card-title { font-size: 0.9rem; font-weight: 600; }
.card-meta { font-size: 0.75rem; color: var(--muted); }
.card-price { font-size: 0.8rem; margin-top: 0.2rem; color: var(--green); }
.card-image { width: 100%; border-radius: 6px; border: 1px solid #111; object-fit: contain; margin-top: 0.25rem; }
.badge { display: inline-block; padding: 0.1rem 0.4rem; border-radius: 999px; font-size: 0.7rem; background: rgba(124,58,237,0.15); color: var(--accent); margin-left: 0.35rem; }
.small-note { font-size: 0.75rem; color: var(--muted); margin-top: 0.75rem; }
@media print {
  body { background: #fff; color: #111; padding: 0.5rem; }
  :root { --bg: #fff; --surface: #f5f5f5; --card: #eee; --text: #111; --muted: #444; }
  .small-note { display: none; }
}
</style>
</head>
<body>
<h1>Newest Cards by Commander</h1>
<p class="subtitle">Showing cards from <strong>"""
            + _escape(set_label)
            + f"""</strong> (release date {target_released_at}).</p>
<div>
"""
    ]

    for cname in sorted(by_commander.keys()):
        info = by_commander[cname]
        deck = info["deck"]
        cards = info["cards"]

        # Deduplicate by card name while preserving first occurrence (for image/metadata).
        unique: Dict[str, Any] = {}
        for card in cards:
            if getattr(card, "name", None) and card.name not in unique:
                unique[card.name] = card

        cards_unique = list(unique.values())

        d0 = deck
        pic = (d0.commander.card_pic or "").replace(
            "/normal/", "/large/"
        ).replace("/small/", "/large/")
        if not pic and d0.commander.card_pic:
            pic = d0.commander.card_pic
        type_line = (d0.commander.type_line or "").replace(
            "Legendary Creature — ", ""
        )

        html_parts.append('<section class="commander-section">\n')
        html_parts.append('<div class="commander-header">\n')
        if pic:
            html_parts.append(
                f'<img class="commander-pic" src="{_escape(pic)}" alt="{_escape(cname)}" loading="lazy">\n'
            )
        html_parts.append('<div class="commander-meta">\n')
        html_parts.append(f'<h2>{_escape(cname)}</h2>\n')
        if type_line:
            html_parts.append(f'<p>{_escape(type_line)}</p>\n')
        if d0.commander.color_identity:
            html_parts.append(
                f'<p>Color identity: {_escape(str(d0.commander.color_identity))}</p>\n'
            )
        html_parts.append(
            f'<p>New cards: {len(cards_unique)}<span class="badge">{_escape(set_label)}</span></p>\n'
        )
        html_parts.append("</div>\n</div>\n")
        html_parts.append('<div class="card-grid">\n')

        for card in sorted(
            cards_unique, key=lambda c: (-(getattr(c, "price", 0.0) or 0.0), getattr(c, "name", "") or "")
        ):
            html_parts.append('<article class="card-tile">\n')
            html_parts.append(f'<div class="card-title">{_escape(card.name or "")}</div>\n')
            if getattr(card, "type_line", None) or getattr(card, "rarity", None):
                tl = _escape(getattr(card, "type_line", "") or "")
                rarity = _escape(getattr(card, "rarity", "") or "").title()
                meta = tl
                if rarity:
                    meta = f"{tl} · {rarity}" if tl else rarity
                html_parts.append(f'<div class="card-meta">{meta}</div>\n')
            if getattr(card, "price", None) is not None:
                html_parts.append(f'<div class="card-price">${card.price:.2f}</div>\n')
            if getattr(card, "card_pic", None):
                html_parts.append(
                    f'<img class="card-image" src="{_escape(card.card_pic)}" alt="{_escape(card.name or "")}" loading="lazy">\n'
                )
            html_parts.append("</article>\n")

        html_parts.append("</div>\n")
        html_parts.append("</section>\n")

    html_parts.append(
        """</div>
<p class="small-note">Tip: re-run this script after new sets release to refresh this page with newly added cards.</p>
</body>
</html>"""
    )

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(html_parts))
        print(f"Wrote latest-set summary to {path}")
    except OSError as e:
        print(f"Error writing latest-set HTML: {e}")


__all__ = ["write_latest_set_html"]

