"""Decklist parser for Moxfield / Archidekt / MTGO / Arena text exports.

Handles lines like ``1x Sol Ring (CMM) 432 *F* #Ramp``, ``1 Sol Ring``, Archidekt's
``1x Sol Ring (cmm) 432 [Ramp]`` and section headers (``Commander``, ``Deck``, ``Sideboard``).
A line carrying ``# !protect`` / ``#!protect`` is flagged as user-protected.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional

from edhparse.evaluate.models import DeckEntry, Decklist, name_key

_PROTECT_RE = re.compile(r"#\s*!protect\b", re.I)
_QTY_RE = re.compile(r"^\s*(\d+)\s*[xX]?\s+(.+?)\s*$")
_SET_RE = re.compile(r"\s+\([A-Za-z0-9_-]{2,8}\)(\s+[A-Za-z0-9★†-]+)?")  # (CMM) 432
_FOIL_RE = re.compile(r"\s+\*[A-Za-z]+\*")  # *F*, *E*, *CMDR*
_TAG_RE = re.compile(r"\s+#.*$")  # #Ramp #!protect
_BRACKET_RE = re.compile(r"\s+\[([^\]]*)\]")  # Archidekt [Category{top}]
_CARET_RE = re.compile(r"\s+\^[^^]*\^")  # Archidekt ^Have,#37d67a^

_COMMANDER_HEADERS = {"commander", "commanders", "commander:", "commanders:"}
_MAIN_HEADERS = {"deck", "deck:", "mainboard", "mainboard:", "main", "main:", "companion", "companion:"}
_SIDEBOARD_HEADERS = {
    "sideboard", "sideboard:", "maybeboard", "maybeboard:", "considering", "considering:",
}
_SKIP_HEADERS = {
    "tokens", "tokens:", "about", "about:",
}

_BASICS = {"plains", "island", "swamp", "mountain", "forest", "wastes"} | {
    f"snow-covered {b}" for b in ("plains", "island", "swamp", "mountain", "forest", "wastes")
}


def split_commander_option(value: Optional[str]) -> List[str]:
    if not value:
        return []
    # Commas also appear inside card names ("Korvold, Fae-Cursed King"), so a single
    # comma-joined value is resolved against Scryfall later (engine.resolve_commanders).
    parts = re.split(r"\s*[;|]\s*|\s+\+\s+", value.strip())
    return [p.strip() for p in parts if p.strip()]


def clean_card_line(line: str) -> Optional[tuple]:
    """Return (quantity, name, protected, tags) or None if the line is not a card line."""
    raw = line.rstrip("\n")
    protected = bool(_PROTECT_RE.search(raw))
    m = _QTY_RE.match(raw)
    if not m:
        return None
    qty = int(m.group(1))
    rest = m.group(2)
    tags: List[str] = []
    for bm in _BRACKET_RE.finditer(rest):
        tags.extend(t.split("{")[0].strip() for t in bm.group(1).split(","))
    rest = _BRACKET_RE.sub("", rest)
    rest = _CARET_RE.sub("", rest)
    tag_m = _TAG_RE.search(rest)
    if tag_m:
        tags.extend(t for t in re.findall(r"#\s*!?([^#]+)", tag_m.group(0)) if t.strip())
        rest = rest[: tag_m.start()]
    is_cmdr_marker = "*CMDR*" in rest.upper()
    rest = _FOIL_RE.sub("", rest)
    rest = _SET_RE.sub("", rest)
    name = rest.strip()
    if not name:
        return None
    if is_cmdr_marker:
        tags.append("Commander")
    return qty, name, protected, [t.strip() for t in tags if t.strip()]


def parse_decklist_lines(lines: Iterable[str], commander_option: Optional[str] = None) -> Decklist:
    commanders = split_commander_option(commander_option)
    section = "main"
    entries: List[DeckEntry] = []
    sideboard_entries: List[DeckEntry] = []
    section_commanders: List[str] = []
    blocks: List[List[DeckEntry]] = [[]]
    warnings: List[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if blocks[-1]:
                blocks.append([])
            continue

        # Check for section headers (optionally preceded by comment slashes or followed by counts)
        head_check = re.sub(r"^//+\s*", "", stripped).strip().lower()
        head_check = re.sub(r"\s*\(\d+\):?$", "", head_check).strip()

        if head_check.startswith("commander:") and stripped[len("commander:"):].strip():
            remainder = stripped[len("commander:"):].strip()
            parsed_rem = clean_card_line(remainder)
            section_commanders.append(parsed_rem[1] if parsed_rem else remainder)
            continue
        if head_check in _COMMANDER_HEADERS:
            section = "commander"
            continue
        if head_check in _MAIN_HEADERS:
            section = "main"
            continue
        if head_check in _SIDEBOARD_HEADERS:
            section = "sideboard"
            continue
        if head_check in _SKIP_HEADERS:
            section = "skip"
            continue
        if stripped.startswith("//"):
            if blocks[-1]:
                blocks.append([])
            continue

        parsed = clean_card_line(stripped)
        if parsed is None:
            low = stripped.lower()
            if low.startswith("name ") or low.startswith("#"):
                continue
            warnings.append(f"Could not parse line: {stripped}")
            continue
        qty, name, protected, tags = parsed
        if section == "skip":
            continue
        if section == "commander" or any(t.lower().startswith("commander") for t in tags):
            section_commanders.append(name)
            continue
        entry = DeckEntry(name=name, quantity=qty, protected=protected, tags=tags)
        if section == "sideboard":
            sideboard_entries.append(entry)
        else:
            entries.append(entry)
        blocks[-1].append(entry)

    if not commanders:
        commanders = section_commanders
    if not commanders:
        # MTGO-style and Moxfield exports put the commander(s) in a final block after a blank line.
        tail = [b for b in blocks if b]
        if len(tail) >= 2 and 1 <= len(tail[-1]) <= 2:
            commanders = [e.name for e in tail[-1]]
            tail_ids = {id(e) for e in tail[-1]}
            entries = [e for e in entries if id(e) not in tail_ids]
            sideboard_entries = [e for e in sideboard_entries if id(e) not in tail_ids]

    cmd_keys = {name_key(c) for c in commanders}
    entries = [e for e in entries if name_key(e.name) not in cmd_keys]
    sideboard_entries = [e for e in sideboard_entries if name_key(e.name) not in cmd_keys]

    merged: dict = {}
    for e in entries:
        key = name_key(e.name)
        if key in merged:
            merged[key].quantity += e.quantity
            merged[key].protected = merged[key].protected or e.protected
            merged[key].tags.extend(e.tags)
        else:
            merged[key] = e

    merged_sb: dict = {}
    for e in sideboard_entries:
        key = name_key(e.name)
        if key in merged_sb:
            merged_sb[key].quantity += e.quantity
            merged_sb[key].protected = merged_sb[key].protected or e.protected
            merged_sb[key].tags.extend(e.tags)
        else:
            merged_sb[key] = e

    deck = Decklist(
        commanders=commanders,
        cards=list(merged.values()),
        sideboard=list(merged_sb.values()),
        warnings=warnings,
    )

    if not deck.commanders:
        deck.warnings.append(
            "No commander found: pass --commander, add a 'Commander' section, or put the "
            "commander alone in the last block of the file."
        )
    elif len(deck.commanders) > 2:
        deck.warnings.append(f"Found {len(deck.commanders)} commanders; only the first two are used.")
        deck.commanders = deck.commanders[:2]
    for e in deck.cards:
        if e.quantity > 1 and name_key(e.name) not in _BASICS:
            deck.warnings.append(f"{e.name} appears {e.quantity} times (allowed only for some cards).")
    if deck.total > 100:
        deck.warnings.append(
            f"Deck has {deck.total} cards ({deck.total - 100} over 100, expected 100). "
            f"Evaluating all cards to help find the best {deck.total - 100} cuts."
        )
    elif deck.total < 100:
        deck.warnings.append(f"Deck has {deck.total} cards including commander(s), expected 100.")
    return deck


def parse_decklist_file(path: str | Path, commander_option: Optional[str] = None) -> Decklist:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return parse_decklist_lines(text.splitlines(), commander_option)


def parse_decklist_text(text: str, commander_option: Optional[str] = None) -> Decklist:
    return parse_decklist_lines(text.splitlines(), commander_option)
